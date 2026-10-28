"""设备分析服务：取数 → 规则 → 可选 LLM 解释 → 落库 → critical 自动告警（v3.23）。

诚实边界：
- 证据不足（点数/覆盖率不达标）时只落一条 ``insufficient`` 记录并明确说「证据不足」，
  不调用模型、不生成告警；
- LLM 只做「解释与建议」，发现列表完全由确定性规则产生；模型输出必须引用数字，
  否则回落到规则摘要；
- 每日 token 预算按 ``yard_job_runs`` 中 device_analysis 的消耗统计，超预算自动只出
  规则结论；
- 不做设备控制，不做因果断言。
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.audit.service import log_audit
from app.modules.devices import analysis
from app.modules.devices.models import (
    Device,
    DeviceAlert,
    DeviceAlertRule,
    DeviceAnalysis,
    Reading,
)
from app.modules.llm.schemas import ChatMessage, LLMMetadata, LLMRequest
from app.modules.llm.service import complete_with_logging
from app.modules.yards.settings_service import get_setting

log = get_logger(__name__)

DEFAULT_WINDOW_HOURS = 24
MAX_POINTS_PER_METRIC = 2000
MAX_FINDINGS = 20
MAX_OUTPUT_TOKENS = 700
LLM_ATTEMPTS = 1
SUMMARY_CHARS = 1200
ALERT_DEDUPE_MINUTES = 30
DEVICE_ANALYSIS_JOB = "device_analysis"


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def window_hours(setting: dict[str, Any]) -> int:
    try:
        value = int(setting.get("window_hours") or DEFAULT_WINDOW_HOURS)
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_HOURS
    return max(6, min(value, 168))


def analysis_key(window_to: datetime, granularity: str) -> str:
    return f"{window_to.date().isoformat()}:{granularity}"


# ---------------------------------------------------------------------------
# 取数
# ---------------------------------------------------------------------------


def collect_series(
    db: Session, device: Device, *, window_from: datetime, window_to: datetime
) -> dict[str, list[tuple[datetime, float]]]:
    rows = list(
        db.scalars(
            select(Reading)
            .where(
                Reading.device_id == device.id,
                Reading.collected_at >= window_from,
                Reading.collected_at <= window_to,
                Reading.value.isnot(None),
            )
            .order_by(Reading.collected_at.asc())
            .limit(MAX_POINTS_PER_METRIC * 4)
        )
    )
    series: dict[str, list[tuple[datetime, float]]] = {}
    for row in rows:
        if row.value is None:
            continue
        bucket = series.setdefault(row.metric, [])
        if len(bucket) >= MAX_POINTS_PER_METRIC:
            continue
        at = _as_utc(row.collected_at) or window_to
        bucket.append((at, float(row.value)))
    return series


def _rules_for(db: Session, yard_id: str, device_id: str) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(DeviceAlertRule).where(
                DeviceAlertRule.yard_id == yard_id,
                DeviceAlertRule.enabled.is_(True),
            )
        )
    )
    return [
        {
            "id": rule.id,
            "metric": rule.metric,
            "op": rule.op,
            "threshold": rule.threshold,
            "notify_note": rule.notify_note,
            "device_id": rule.device_id,
        }
        for rule in rows
        if rule.device_id in (None, device_id)
    ]


# ---------------------------------------------------------------------------
# LLM 解释（可关）
# ---------------------------------------------------------------------------


def _budget_used_today(db: Session, yard_id: str, now: datetime) -> int:
    from app.modules.self_improve.models import YardJobRun

    start = datetime.combine(now.date(), datetime.min.time(), tzinfo=UTC)
    rows = list(
        db.scalars(
            select(YardJobRun).where(
                YardJobRun.yard_id == yard_id,
                YardJobRun.kind == DEVICE_ANALYSIS_JOB,
                YardJobRun.started_at >= start,
            )
        )
    )
    return sum(int(row.tokens_used or 0) for row in rows)


def _llm_messages(
    device: Device,
    findings: list[dict[str, Any]],
    metrics: dict[str, Any],
    quality: dict[str, Any],
) -> list[ChatMessage]:
    payload = {
        "device": {"name": device.name, "type": device.device_type},
        "findings": findings,
        "metrics": metrics,
        "quality": {
            "score": quality.get("score"),
            "coverage": quality.get("coverage"),
            "notes": quality.get("notes"),
        },
    }
    system = (
        "你是农业物联网数据分析助手。只能基于给定 JSON 中的读数统计与规则发现做解释，"
        "不得编造未出现的指标或数值。输出 JSON 对象（不要代码块围栏）："
        '{"summary":str(≤300字),"actions":[str(≤3条,每条≤80字)],"cautions":[str(≤2条,每条≤80字)]}。'
        "要求：summary 必须包含至少一个具体数值与时间（取自证据）；"
        "actions 只给核查/农事建议，不涉及设备控制；"
        "用药类建议必须写明「需经作物诊断与专家复核」；"
        "证据不足时 cautions 要说明还需要哪些数据。全部中文。"
    )
    user = "数据（JSON）：\n" + json.dumps(payload, ensure_ascii=False)[:8000]
    return [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)]


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse_json(content: str) -> dict[str, Any] | None:
    text = _FENCE_RE.sub("", (content or "").strip()).strip()
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _clean(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def explain_with_llm(
    db: Session,
    *,
    device: Device,
    findings: list[dict[str, Any]],
    metrics: dict[str, Any],
    quality: dict[str, Any],
    actor_id: str | None,
    request_id: str,
) -> tuple[str, str | None, int]:
    """返回 (摘要, 模型名, tokens)。失败时返回空摘要由调用方回落规则摘要。"""
    for _ in range(LLM_ATTEMPTS):
        try:
            resp = asyncio.run(
                complete_with_logging(
                    db,
                    LLMRequest(
                        messages=_llm_messages(device, findings, metrics, quality),
                        max_tokens=MAX_OUTPUT_TOKENS,
                        metadata=LLMMetadata(
                            yard_id=device.yard_id,
                            user_id=actor_id,
                            request_id=request_id,
                            purpose="device_analysis",
                            agent_ref="devices.analysis",
                        ),
                    ),
                )
            )
        except AppError as e:
            log.info("device analysis llm unavailable: %s", e.code)
            return "", None, 0
        except Exception as e:  # noqa: BLE001 - 模型异常不影响规则结论
            log.warning("device analysis llm failed: %s", e)
            return "", None, 0
        payload = _parse_json(resp.content or "")
        used = int(resp.input_tokens or 0) + int(resp.output_tokens or 0)
        if not payload:
            return "", None, used
        summary = _clean(payload.get("summary"), SUMMARY_CHARS)
        actions = [
            _clean(item, 120) for item in (payload.get("actions") or []) if _clean(item, 120)
        ][:3]
        cautions = [
            _clean(item, 120) for item in (payload.get("cautions") or []) if _clean(item, 120)
        ][:2]
        if findings and not re.search(r"\d", summary):
            return "", None, used
        parts = [summary] if summary else []
        if actions:
            parts.append("建议：" + "；".join(actions))
        if cautions:
            parts.append("注意：" + "；".join(cautions))
        return "\n".join(parts)[:2000], resp.model or None, used
    return "", None, 0


# ---------------------------------------------------------------------------
# 落库与告警
# ---------------------------------------------------------------------------


def _raise_critical_alerts(
    db: Session,
    *,
    device: Device,
    analysis_row: DeviceAnalysis,
    findings: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    request_id: str,
) -> int:
    """critical 发现转设备告警，进入既有「告警 → 一键工单」链路。"""
    created = 0
    now = datetime.now(UTC)
    cutoff = now - timedelta(minutes=ALERT_DEDUPE_MINUTES)
    rule_by_metric = {str(item["metric"]): item for item in rules}
    for finding in findings:
        if finding.get("level") != "critical":
            continue
        metric = str(finding.get("metric") or "")
        rule = rule_by_metric.get(metric)
        if rule is not None:
            deduped = db.scalars(
                select(DeviceAlert.id)
                .where(
                    DeviceAlert.rule_id == rule["id"],
                    DeviceAlert.device_id == device.id,
                    DeviceAlert.triggered_at >= cutoff,
                )
                .limit(1)
            ).first()
            if deduped:
                continue
        value = None
        for item in finding.get("evidence") or []:
            if isinstance(item, dict) and isinstance(item.get("value"), int | float):
                value = float(item["value"])
        text = (
            f"【设备分析】{device.name}：{finding.get('title')}。"
            f"分析记录 {analysis_row.id}，请结合数据核查。"
        )[:500]
        db.add(
            DeviceAlert(
                yard_id=device.yard_id,
                rule_id=rule["id"] if rule else None,
                device_id=device.id,
                metric=metric or "_analysis",
                value=value,
                triggered_at=now,
                notified_text=text,
            )
        )
        created += 1
        log_audit(
            db,
            yard_id=device.yard_id,
            actor_id=None,
            action="device_analysis_alert",
            resource_type="device_analysis",
            resource_id=analysis_row.id,
            result="success",
            request_id=request_id,
            metadata={"metric": metric, "code": finding.get("code")},
        )
        try:
            from app.modules.push import service as push_service

            push_service.dispatch(
                db,
                yard_id=device.yard_id,
                event_type="device_alert",
                payload={
                    "title": text[:200],
                    "device_id": device.id,
                    "metric": metric,
                    "value": value,
                    "analysis_id": analysis_row.id,
                },
                request_id=request_id,
            )
        except Exception as e:  # noqa: BLE001 - 推送失败不影响分析
            log.warning("device analysis push failed: %s", e)
    return created


def run_analysis(
    db: Session,
    device: Device,
    *,
    trigger: str,
    actor_id: str | None,
    request_id: str,
    now: datetime | None = None,
    force: bool = False,
) -> tuple[DeviceAnalysis, bool]:
    """执行一次分析；返回 (记录, 是否新建)。同一天重复运行幂等返回既有记录。"""
    now = _as_utc(now) or datetime.now(UTC)
    setting = get_setting(db, device.yard_id, "device_analysis")
    hours = window_hours(setting)
    window_to = now.replace(minute=0, second=0, microsecond=0)
    window_from = window_to - timedelta(hours=hours)
    key = analysis_key(window_to, "hour")
    existing = db.scalar(
        select(DeviceAnalysis).where(
            DeviceAnalysis.device_id == device.id,
            DeviceAnalysis.analysis_key == key,
        )
    )
    if existing is not None and not force:
        return existing, False

    series = collect_series(db, device, window_from=window_from, window_to=window_to)
    if not series:
        raise AppError(
            "DEVICE_ANALYSIS_INSUFFICIENT_DATA",
            f"设备「{device.name}」在最近 {hours} 小时没有任何读数，无法分析。",
            http_status=422,
        )
    quality = analysis.assess_quality(series, window_from=window_from, window_to=window_to)
    enough = analysis.has_enough_evidence(series, quality)
    metrics = {
        metric: stat.to_dict()
        for metric, items in series.items()
        if (stat := analysis.summarize(metric, items)) is not None
    }
    rules = _rules_for(db, device.yard_id, device.id)
    findings: list[dict[str, Any]] = []
    summary = ""
    level = "info"
    status = "ok"
    if not enough:
        status = "insufficient"
        summary = (
            f"{device.name} 近 {hours} 小时只有 {quality.get('points')} 个数值读数、"
            f"覆盖 {quality.get('covered_hours')}/{quality.get('hours')} 小时，"
            "证据不足，本轮不产出结论。请检查设备供电与网络后重试。"
        )
    else:
        findings = analysis.dedupe(
            [
                *analysis.detect_thresholds(series, rules),
                *analysis.detect_trends(series),
                *analysis.detect_anomalies(series),
                *analysis.detect_quality_findings(quality),
                *analysis.detect_combos(series),
            ]
        )[:MAX_FINDINGS]
        level = analysis.overall_level(findings) if findings else "info"
        summary = analysis.rule_summary(findings, device_name=device.name)

    model: str | None = None
    tokens_used = 0
    if (
        status == "ok"
        and bool(setting.get("explain_with_llm"))
        and int(setting.get("daily_budget_tokens") or 0)
        > _budget_used_today(db, device.yard_id, now)
    ):
        llm_summary, model, tokens_used = explain_with_llm(
            db,
            device=device,
            findings=findings,
            metrics=metrics,
            quality=quality,
            actor_id=actor_id,
            request_id=request_id,
        )
        if llm_summary:
            summary = llm_summary

    if existing is not None:  # force 重跑：覆盖同键记录，不新增
        row = existing
        row.window_from = window_from
        row.window_to = window_to
        row.status = status
        row.level = level
        row.findings_json = findings
        row.metrics_json = metrics
        row.quality_json = quality
        row.summary = summary
        row.model = model
        row.tokens_used = tokens_used
        row.trigger = trigger
        row.actor_id = actor_id
    else:
        row = DeviceAnalysis(
            yard_id=device.yard_id,
            device_id=device.id,
            window_from=window_from,
            window_to=window_to,
            granularity="hour",
            analysis_key=key,
            status=status,
            level=level,
            findings_json=findings,
            metrics_json=metrics,
            quality_json=quality,
            summary=summary,
            model=model,
            tokens_used=tokens_used,
            trigger=trigger,
            actor_id=actor_id,
        )
        db.add(row)
    db.flush()
    alerts_created = 0
    if status == "ok":
        alerts_created = _raise_critical_alerts(
            db,
            device=device,
            analysis_row=row,
            findings=findings,
            rules=rules,
            request_id=request_id,
        )
    log_audit(
        db,
        yard_id=device.yard_id,
        actor_id=actor_id,
        action="device_analysis_run",
        resource_type="device_analysis",
        resource_id=row.id,
        result="success" if status == "ok" else status,
        request_id=request_id,
        metadata={
            "device_id": device.id,
            "trigger": trigger,
            "status": status,
            "level": level,
            "findings": len(findings),
            "alerts": alerts_created,
            "window_hours": hours,
        },
    )
    return row, True


# ---------------------------------------------------------------------------
# 出参
# ---------------------------------------------------------------------------


def analysis_to_dict(db: Session, row: DeviceAnalysis) -> dict[str, Any]:
    device = db.get(Device, row.device_id)
    return {
        "analysis_id": row.id,
        "yard_id": row.yard_id,
        "device_id": row.device_id,
        "device_name": device.name if device else row.device_id,
        "window_from": row.window_from.isoformat(),
        "window_to": row.window_to.isoformat(),
        "granularity": row.granularity,
        "status": row.status,
        "level": row.level,
        "level_label": analysis.LEVEL_LABELS.get(row.level, row.level),
        "findings": row.findings_json or [],
        "metrics": row.metrics_json or {},
        "quality": row.quality_json or {},
        "summary": row.summary,
        "model": row.model,
        "tokens_used": row.tokens_used,
        "trigger": row.trigger,
        "created_at": row.created_at.isoformat(),
    }


def brief(row: DeviceAnalysis | None) -> dict[str, Any] | None:
    """设备卡片用：最近一次分析的摘要（不含发现明细）。"""
    if row is None:
        return None
    return {
        "analysis_id": row.id,
        "status": row.status,
        "level": row.level,
        "level_label": analysis.LEVEL_LABELS.get(row.level, row.level),
        "summary": row.summary,
        "findings_count": len(row.findings_json or []),
        "created_at": row.created_at.isoformat(),
    }


def list_analyses(db: Session, yard_id: str, device_id: str, limit: int = 20) -> list[dict]:
    rows = list(
        db.scalars(
            select(DeviceAnalysis)
            .where(DeviceAnalysis.yard_id == yard_id, DeviceAnalysis.device_id == device_id)
            .order_by(DeviceAnalysis.created_at.desc())
            .limit(max(1, min(limit, 100)))
        )
    )
    return [analysis_to_dict(db, row) for row in rows]


def latest_map(db: Session, yard_id: str, device_ids: list[str]) -> dict[str, DeviceAnalysis]:
    """每台设备最近一条分析（设备少，逐台查一条即可）。"""
    latest: dict[str, DeviceAnalysis] = {}
    for device_id in device_ids:
        row = db.scalar(
            select(DeviceAnalysis)
            .where(DeviceAnalysis.yard_id == yard_id, DeviceAnalysis.device_id == device_id)
            .order_by(DeviceAnalysis.created_at.desc())
            .limit(1)
        )
        if row is not None:
            latest[device_id] = row
    return latest


def insights(db: Session, yard_id: str, limit: int = 10) -> dict[str, Any]:
    """概览卡：只回需要人处理的发现（watch/warning/critical），按等级与时间排序。"""
    rows = list(
        db.scalars(
            select(DeviceAnalysis)
            .where(
                DeviceAnalysis.yard_id == yard_id,
                DeviceAnalysis.status == "ok",
                DeviceAnalysis.level.in_(("watch", "warning", "critical")),
            )
            .order_by(DeviceAnalysis.created_at.desc())
            .limit(200)
        )
    )
    latest: dict[str, DeviceAnalysis] = {}
    for row in rows:  # 已按时间倒序，首条即该设备最近一条
        latest.setdefault(row.device_id, row)
    items = []
    counts = {"watch": 0, "warning": 0, "critical": 0}
    for row in latest.values():
        counts[row.level] = counts.get(row.level, 0) + 1
        device = db.get(Device, row.device_id)
        findings = row.findings_json or []
        items.append(
            {
                "analysis_id": row.id,
                "device_id": row.device_id,
                "device_name": device.name if device else row.device_id,
                "level": row.level,
                "level_label": analysis.LEVEL_LABELS.get(row.level, row.level),
                "summary": row.summary,
                "findings_count": len(findings),
                "top_finding": findings[0]["title"] if findings else "",
                "created_at": row.created_at.isoformat(),
            }
        )
    items.sort(
        key=lambda item: (analysis.LEVEL_ORDER.get(str(item.get("level")), 0), item["created_at"]),
        reverse=True,
    )
    return {"items": items[: max(1, min(limit, 50))], "counts": counts}
