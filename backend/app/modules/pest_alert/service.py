"""虫情预警业务逻辑（v3.76 真实实现，替换脚手架 FakeProvider 版）。

数据链路（全部真实、小院隔离）：
  1. 本院数据：近 7 天 FarmAlert（虫情/天气/设备事件）+ 各设备最新读数摘要；
  2. 知识检索：knowledge.search（crop/query 聚合查询）；
  3. 大模型：llm.service.complete_with_logging 生成结构化预警（严格 JSON，3 次纠错）；
  4. 诚实降级：模型不可用/解析失败时按本院告警规则拼装保守结论，
     warnings 明确标注「非 AI 结论」，绝不假装智能预警。

等级口径（确定性规则兜底时使用）：
  critical 告警存在 → warning；存在虫/病相关告警 → watch；否则 none（数据不足时明说）。
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.farm.models import FarmAlert
from app.modules.knowledge.service import search as knowledge_search
from app.modules.pest_alert.schemas import PestAlertInput, PestAlertOutput

# llm 模块级导入（与 qa_agent 同构）：缺失时 None，运行期诚实降级；测试可 monkeypatch
complete_with_logging: Any
try:
    from app.modules.llm.service import complete_with_logging
except ImportError:  # pragma: no cover - 降级分支
    complete_with_logging = None

log = get_logger(__name__)

#: 近 N 天的告警/读数作为预警上下文
LOOKBACK_DAYS = 7
#: 最多带入上下文的告警条数
MAX_ALERTS = 10
#: 虫/病相关告警标题关键词（规则降级用）
PEST_KEYWORDS = ("虫", "病", "蛾", "螟", "虱", "蚜", "螨", "蝇", "蛆", "鼠")

DISCLAIMER = (
    "本预警由 AI 基于本院监测数据与知识库生成，仅供学习参考，不构成植保专业建议；"
    "用药与防治决策请咨询当地植保部门或持证专家。"
)
RULE_DISCLAIMER = (
    "本结论为基于本院告警的规则摘要（非 AI 生成），仅供学习参考，不构成植保专业建议；"
    "用药与防治决策请咨询当地植保部门或持证专家。"
)
LLM_UNAVAILABLE_WARNING = "模型服务暂不可用，本结论为规则摘要（非 AI 生成），请人工核验。"
LLM_PARSE_FAILED_WARNING = "模型输出未通过结构校验，已降级为规则摘要，请人工核验。"
NO_DATA_NOTE = "本院近 7 天无告警与设备读数，以下结论仅基于知识库与您的描述。"


def _collect_yard_evidence(db: Session, yard_id: str) -> list[dict]:
    """采集本院数据证据：近 7 天告警 + 各设备最新读数。失败降级为空（不阻断）。"""
    evidence: list[dict] = []
    since = datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)
    try:
        alerts = list(
            db.scalars(
                select(FarmAlert)
                .where(FarmAlert.yard_id == yard_id, FarmAlert.created_at >= since)
                .order_by(FarmAlert.created_at.desc())
                .limit(MAX_ALERTS)
            )
        )
        evidence.extend(
            {
                "kind": "alert",
                "title": a.title,
                "severity": a.severity,
                "detail": (a.detail or "")[:160],
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in alerts
        )
    except Exception as e:  # noqa: BLE001 —— 数据采集失败不阻断预警
        log.warning("pest_alert farm alerts query failed: %s", e)

    # 设备最新读数摘要：失败降级为空（无设备是正常场景，不告警）
    try:
        from app.modules.devices.models import Device
        from app.modules.devices.service import latest_summary

        devices = list(db.scalars(select(Device).where(Device.yard_id == yard_id).limit(20)))
        for d in devices:
            for row in latest_summary(db, d):
                evidence.append(
                    {
                        "kind": "reading",
                        "device": d.name,
                        "metric": row.get("metric_name") or row.get("metric"),
                        "value": row.get("value"),
                        "unit": row.get("unit"),
                        "collected_at": row.get("collected_at"),
                    }
                )
    except Exception as e:  # noqa: BLE001
        log.warning("pest_alert device readings skipped: %s", e)
    return evidence


def _alert_level_rules(evidence: list[dict]) -> tuple[str, list[str]]:
    """确定性规则：由本院告警推导保守预警等级（降级路径用，绝不虚高）。"""
    alert_items = [e for e in evidence if e.get("kind") == "alert"]
    pest_alerts = [
        a for a in alert_items if any(k in str(a.get("title", "")) for k in PEST_KEYWORDS)
    ]
    critical_alerts = [a for a in alert_items if a.get("severity") == "critical"]
    risks: list[str] = []
    if critical_alerts:
        risks.append(f"本院存在 {len(critical_alerts)} 条严重告警（详见证据），需优先核实处置。")
        return "warning", risks
    if pest_alerts:
        risks.append(
            f"近 7 天本院有 {len(pest_alerts)} 条虫/病相关告警，建议到田核实虫口密度与危害状。"
        )
        return "watch", risks
    return "none", risks


def _rule_based_output(
    parsed: PestAlertInput,
    sources: list[dict],
    evidence: list[dict],
    warnings: list[str],
) -> dict[str, Any]:
    """降级输出：规则等级 + 检索摘要，诚实标注非 AI 结论。"""
    level, risks = _alert_level_rules(evidence)
    pest_alerts = [e for e in evidence if e.get("kind") == "alert"]
    readings = [e for e in evidence if e.get("kind") == "reading"]
    parts: list[str] = []
    scope = "、".join(x for x in (parsed.crop, parsed.query) if x)
    if scope:
        parts.append(f"关注对象：{scope}。")
    if pest_alerts:
        parts.append(f"近 7 天本院告警 {len(pest_alerts)} 条；")
    if readings:
        parts.append(f"已采集 {len(readings)} 项设备最新读数；")
    if not evidence:
        parts.append(NO_DATA_NOTE)
    if sources:
        parts.append(f"知识库命中 {len(sources)} 条相关资料，见引用。")
    answer = ("".join(parts) or "本院暂无虫情相关数据。") + RULE_DISCLAIMER
    summary = risks[0] if risks else "暂未发现需立即处置的虫情信号（基于规则摘要）。"
    recommendations = [
        "到田核查告警所涉地块，确认虫口密度与危害状；",
        "拍照上传「作物诊断」，请专家复核后再决定是否用药；",
    ]
    return {
        "answer": answer,
        "level": level,
        "summary": summary,
        "risks": risks,
        "recommendations": recommendations,
        "evidence": evidence[:20],
        "sources": sources,
        "confidence": 0.3,
        "warnings": warnings,
    }


def _extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出中提取 JSON 对象（容忍 ```json 围栏与前后缀文本）。"""
    if not text:
        return None
    candidates = [text]
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.S)
    if fence:
        candidates.insert(0, fence.group(1))
    brace = re.search(r"\{.*\}", text, re.S)
    if brace:
        candidates.insert(0, brace.group(0))
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _build_user_prompt(
    parsed: PestAlertInput, evidence: list[dict], sources: list[dict], error_note: str
) -> str:
    """构造用户提示词：本院数据 + 知识摘录 + 严格 JSON 输出要求。"""
    scope = "、".join(x for x in (parsed.crop, parsed.query) if x) or "本院总体虫情风险"
    data_lines = []
    for e in evidence[:20]:
        if e.get("kind") == "alert":
            data_lines.append(f"- 告警[{e.get('severity')}] {e.get('title')}：{e.get('detail')}")
        else:
            data_lines.append(
                f"- 读数 {e.get('device')} {e.get('metric')}={e.get('value')}{e.get('unit') or ''}"
                f"（{e.get('collected_at')}）"
            )
    if not data_lines:
        data_lines.append("- （本院近 7 天无告警与读数）")
    src_lines = [f"- {s.get('title')}：{str(s.get('quote') or '')[:160]}" for s in sources[:8]]
    if not src_lines:
        src_lines.append("- （知识库未命中）")
    parts = [
        "你是科技小院的植保预警助手。请基于以下材料生成虫情预警。",
        f"关注对象：{scope}",
        "",
        "本院近 7 天数据：",
        *data_lines,
        "",
        "知识库摘录：",
        *src_lines,
        "",
    ]
    if error_note:
        parts.append(error_note)
        parts.append("")
    parts.append(
        "输出 JSON，字段严格为：\n"
        "{\n"
        '  "level": "none|watch|warning|critical",\n'
        '  "summary": "一句话结论（不超过 60 字）",\n'
        '  "risks": ["风险因素 1", "风险因素 2"],\n'
        '  "recommendations": ["防治建议 1", "防治建议 2"]\n'
        "}\n"
        "只输出一个 JSON 对象，不要任何解释、不要 markdown 代码块。"
    )
    return "\n".join(parts)


def _llm_structured(
    db: Session,
    yard_id: str,
    request_id: str,
    parsed: PestAlertInput,
    evidence: list[dict],
    sources: list[dict],
) -> dict[str, Any]:
    """真 LLM 结构化生成；模型不可用抛异常，解析失败重试 3 次后抛错。"""
    if complete_with_logging is None:
        raise AppError("LLM_UNAVAILABLE", "模型服务未接入。")
    from app.modules.llm.schemas import ChatMessage, LLMMetadata, LLMRequest

    system_prompt = (
        "你是科技小院平台的植保预警引擎。基于给定的本院监测数据与知识库摘录，"
        "输出保守、可核验的虫情预警。等级判定口径：critical=需立即处置；warning=已出现"
        "明确风险信号；watch=需到田核实；none=暂无风险信号。数据不足时宁可给 watch 并在"
        "risks 里说明，不得编造数据或虫口数字。"
    )
    error_note = ""
    last_error = ""
    obj: dict[str, Any] | None = None
    for _attempt in range(3):
        user_prompt = _build_user_prompt(parsed, evidence, sources, error_note)
        req = LLMRequest(
            messages=[
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=user_prompt),
            ],
            metadata=LLMMetadata(yard_id=yard_id, request_id=request_id, purpose="pest_alert"),
        )
        result = complete_with_logging(db, req)
        text = (
            getattr(result, "content", None)
            or getattr(result, "text", None)
            or getattr(result, "answer", None)
        )
        obj = _extract_json(str(text or ""))
        if obj is None:
            last_error = "输出不是合法 JSON"
            error_note = "上一次输出不是合法 JSON，请严格只输出一个 JSON 对象。"
            continue
        try:
            PestAlertOutput(
                answer="",
                level=obj.get("level", "none"),
                summary=str(obj.get("summary") or "")[:200],
                risks=[str(x)[:160] for x in (obj.get("risks") or []) if str(x).strip()][:8],
                recommendations=[
                    str(x)[:160] for x in (obj.get("recommendations") or []) if str(x).strip()
                ][:8],
                evidence=evidence[:20],
                sources=sources,
                confidence=0.7,
                warnings=[],
            )
        except Exception as e:  # noqa: BLE001 —— 字段校验失败回灌重试
            obj = None
            last_error = f"字段校验失败：{e}"
            error_note = "上一次输出的字段不合规，请按给定字段与枚举重新输出 JSON。"
            continue
        break
    if obj is None:
        raise AppError("PEST_ALERT_PARSE_FAILED", f"模型输出结构化失败：{last_error}")
    return obj


async def run_pest_alert(
    db: Session,
    yard_id: str,
    user_id: str,
    input_data: dict[str, Any],
    request_id: str = "req_unknown",
) -> dict[str, Any]:
    """虫情预警入口：真实数据 + 真 LLM，失败诚实降级。返回符合 PestAlertOutput 的 dict。"""
    parsed = PestAlertInput(**(input_data or {}))
    try:
        evidence = _collect_yard_evidence(db, yard_id)
    except Exception as e:  # noqa: BLE001
        log.warning("pest_alert evidence collection failed: %s", e)
        evidence = []

    ctx = YardContext(yard_id=yard_id, user_id=user_id, roles=[], request_id=request_id)
    sources: list[dict] = []
    query_text = " ".join(x for x in (parsed.crop, parsed.query, "虫害 预警 防治") if x).strip()
    if query_text:
        try:
            hits = knowledge_search(db, ctx, query_text, top_k=parsed.top_k)
            sources = [
                {
                    "title": h.title,
                    "source": h.source,
                    "quote": h.quote,
                    "score": h.score,
                    "trust_level": h.trust_level,
                }
                for h in hits
            ]
        except Exception as e:  # noqa: BLE001
            log.warning("pest_alert knowledge.search failed: %s", e)
            sources = []

    warnings: list[str] = []
    try:
        obj = _llm_structured(db, yard_id, request_id, parsed, evidence, sources)
        level = obj.get("level", "none")
        summary = str(obj.get("summary") or "")
        risks = [str(x) for x in (obj.get("risks") or [])]
        recs = [str(x) for x in (obj.get("recommendations") or [])]
        confidence = 0.7
    except Exception as e:  # noqa: BLE001 —— 模型不可用/解析失败：诚实降级
        log.warning("pest_alert llm path degraded: %s", e)
        code = str(getattr(e, "code", "") or "")
        if code == "PEST_ALERT_PARSE_FAILED" or "结构化失败" in str(e):
            warnings.append(LLM_PARSE_FAILED_WARNING)
        else:
            warnings.append(LLM_UNAVAILABLE_WARNING)
        return _rule_based_output(parsed, sources, evidence, warnings)

    if not evidence:
        warnings.append(NO_DATA_NOTE)
    parts = []
    scope = "、".join(x for x in (parsed.crop, parsed.query) if x)
    if scope:
        parts.append(f"关注对象：{scope}。")
    parts.append(summary)
    if risks:
        parts.append("风险：" + "；".join(risks[:4]) + "。")
    if recs:
        parts.append("建议：" + "；".join(recs[:4]) + "。")
    answer = "".join(parts) + DISCLAIMER
    return {
        "answer": answer,
        "level": level,
        "summary": summary,
        "risks": risks,
        "recommendations": recs,
        "evidence": evidence[:20],
        "sources": sources,
        "confidence": confidence,
        "warnings": warnings,
    }
