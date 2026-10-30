"""Agent 自动执行（v3.28）：证据 → 规则 → 按策略执行 → 推送 → 留档。

执行口径：

- **规则先行**：候选动作全部来自确定性规则；模型只把发现写成一段中文结论，
  不得新增动作（与设备分析同一护栏）；
- **三档策略**：``auto`` 直接执行、``confirm`` 落「待确认」由人点一下、``off`` 不产出；
- **默认保守**：告警与系统观察记录默认自动，工单/诊断/药剂/地块默认待确认；
- **幂等降噪**：同一 ``dedupe_key`` 在 ``dedupe_hours`` 内不重复产出，避免定时任务刷屏；
- **可追溯**：每条产物都带 ``agent_run_id``，推送与审计都能回到这次运行。
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.audit.service import log_audit
from app.modules.farm import agent_evidence, agent_rules
from app.modules.farm.models import (
    ALERT_SEVERITIES,
    FARM_AGENT_ACTION_KINDS,
    FarmAgentAction,
    FarmAgentRun,
    FarmAlert,
    FarmPlot,
    FarmRecord,
    PesticideProduct,
    WorkOrder,
    WorkOrderEvent,
)
from app.modules.llm.schemas import ChatMessage, LLMMetadata, LLMRequest
from app.modules.llm.service import complete_with_logging
from app.modules.yards.settings_service import get_setting

log = get_logger(__name__)

JOB_KIND = "farm_agent"
DEFAULT_DEDUPE_HOURS = 24
MAX_FINDINGS = 20
MAX_ACTIONS_PER_RUN = 40
SUMMARY_CHARS = 1500
LLM_MAX_TOKENS = 700
#: 模型总结的单轮硬超时（秒）；超时回落到规则摘要，不阻塞动作执行
LLM_TIMEOUT_S = 20
ACTION_SOURCE = "agent"

_DEFAULT_POLICIES = {
    "alert": "auto",
    "work_order": "confirm",
    "diagnosis": "confirm",
    "record": "auto",
    "pesticide": "confirm",
    "plot": "confirm",
}

_TARGET_TYPES = {
    "alert": "farm_alert",
    "work_order": "work_order",
    "diagnosis": "crop_diagnosis",
    "record": "farm_record",
    "pesticide": "pesticide_product",
    "plot": "farm_plot",
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def policy_for(setting: dict[str, Any], kind: str) -> str:
    """读取某个动作类型的策略；未知类型按 confirm（不擅自自动执行）。"""
    actions = setting.get("actions")
    value = str((actions or {}).get(kind) or "").strip()
    if value in ("auto", "confirm", "off"):
        return value
    return _DEFAULT_POLICIES.get(kind, "confirm")


def _dedupe_hours(setting: dict[str, Any]) -> int:
    try:
        value = int(setting.get("dedupe_hours") or DEFAULT_DEDUPE_HOURS)
    except (TypeError, ValueError):
        return DEFAULT_DEDUPE_HOURS
    return max(1, min(value, 168))


# ---------------------------------------------------------------------------
# 出参
# ---------------------------------------------------------------------------


def action_out(row: FarmAgentAction) -> dict[str, Any]:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "kind": row.kind,
        "kind_label": agent_rules.ACTION_LABELS.get(row.kind, row.kind),
        "rule_code": row.rule_code,
        "rule_label": agent_rules.RULE_LABELS.get(row.rule_code, row.rule_code),
        "status": row.status,
        "policy": row.policy,
        "severity": row.severity,
        "title": row.title,
        "detail": row.detail,
        "payload": row.payload_json or {},
        "target_type": row.target_type,
        "target_id": row.target_id,
        "dedupe_key": row.dedupe_key,
        "error": row.error,
        "confirmed_by": row.confirmed_by,
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def run_out(row: FarmAgentRun, actions: list[FarmAgentAction] | None = None) -> dict[str, Any]:
    data = {
        "run_id": row.id,
        "trigger": row.trigger,
        "status": row.status,
        "level": row.level,
        "level_label": agent_rules.LEVEL_LABELS.get(row.level, row.level),
        "window_from": row.window_from.isoformat() if row.window_from else None,
        "window_to": row.window_to.isoformat() if row.window_to else None,
        "summary": row.summary,
        "evidence": row.evidence_json or {},
        "findings": row.findings_json or [],
        "model": row.model,
        "tokens_used": int(row.tokens_used or 0),
        "error": row.error,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "duration_ms": int(row.duration_ms or 0),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    if actions is not None:
        data["actions"] = [action_out(a) for a in actions]
    return data


# ---------------------------------------------------------------------------
# 模型解释（可选）：只总结，不产生动作
# ---------------------------------------------------------------------------


def _budget_used_today(db: Session, yard_id: str, now: datetime) -> int:
    start = datetime.combine(now.date(), datetime.min.time(), tzinfo=UTC)
    rows = list(
        db.scalars(
            select(FarmAgentRun).where(
                FarmAgentRun.yard_id == yard_id,
                FarmAgentRun.started_at >= start,
            )
        )
    )
    return sum(int(row.tokens_used or 0) for row in rows)


def _llm_messages(findings: list[dict[str, Any]], evidence: dict[str, Any]) -> list[ChatMessage]:
    compact = {
        "findings": [
            {
                "code": item.get("code"),
                "level": item.get("level"),
                "title": item.get("title"),
                "detail": item.get("detail"),
                "suggestion": item.get("suggestion"),
            }
            for item in findings
        ],
        "devices": [
            {"name": d.get("name"), "type": d.get("type"), "plot": d.get("plot_name")}
            for d in (evidence.get("devices") or [])
        ],
        "weather": evidence.get("weather"),
        "warnings": evidence.get("warnings"),
    }
    system = (
        "你是科技小院的农事调度助手。只能依据给定 JSON 中的规则发现与证据写一段中文结论，"
        "不得新增未列出的预警、不得编造数值或农事操作。输出 JSON 对象（不要代码块围栏）："
        '{"summary":str(≤350字),"actions":[str(≤3条,每条≤80字)],"cautions":[str(≤2条,每条≤80字)]}。'
        "要求：summary 必须引用至少一个具体数值或单据事实；"
        "actions 只能是预防性检查与安排建议，不涉及设备控制；"
        "用药类建议必须写明「需经作物诊断与专家复核」；"
        "warnings 中的证据缺口要在 cautions 里如实说明。全部中文。"
    )
    user = "数据（JSON）：\n" + json.dumps(compact, ensure_ascii=False)[:9000]
    return [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)]


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse_json(content: str) -> dict[str, Any] | None:
    text = _FENCE_RE.sub("", (content or "").strip()).strip()
    for candidate in (text, text[text.find("{") : text.rfind("}") + 1] if "{" in text else text):
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
    yard_id: str,
    findings: list[dict[str, Any]],
    evidence: dict[str, Any],
    actor_id: str | None,
    request_id: str,
) -> tuple[str, str | None, int]:
    """返回 (摘要, 模型, tokens)；任何失败都回落到规则摘要。"""

    async def _call():
        # 上游偶发长尾：加硬超时，避免一轮自动执行被模型拖到分钟级
        return await asyncio.wait_for(
            complete_with_logging(
                db,
                LLMRequest(
                    messages=_llm_messages(findings, evidence),
                    max_tokens=LLM_MAX_TOKENS,
                    metadata=LLMMetadata(
                        yard_id=yard_id,
                        user_id=actor_id,
                        request_id=request_id,
                        purpose="farm_agent",
                        agent_ref="farm.automation",
                    ),
                ),
            ),
            timeout=LLM_TIMEOUT_S,
        )

    try:
        resp = asyncio.run(_call())
    except AppError as e:
        log.info("farm agent llm unavailable: %s", e.code)
        return "", None, 0
    except Exception as e:  # noqa: BLE001 —— 模型异常不影响规则结论
        log.warning("farm agent llm failed: %s", e)
        return "", None, 0
    used = int(resp.input_tokens or 0) + int(resp.output_tokens or 0)
    payload = _parse_json(resp.content or "")
    if not payload:
        return "", None, used
    parts = [_clean(payload.get("summary"), SUMMARY_CHARS)]
    actions = [_clean(i, 120) for i in (payload.get("actions") or []) if _clean(i, 120)][:3]
    cautions = [_clean(i, 120) for i in (payload.get("cautions") or []) if _clean(i, 120)][:2]
    if actions:
        parts.append("建议：" + "；".join(actions))
    if cautions:
        parts.append("注意：" + "；".join(cautions))
    summary = "\n".join(p for p in parts if p)[:SUMMARY_CHARS]
    return summary, resp.model, used


# ---------------------------------------------------------------------------
# 动作执行
# ---------------------------------------------------------------------------


def _evidence_lines(finding: dict[str, Any]) -> str:
    lines: list[str] = []
    for item in (finding.get("evidence") or [])[:4]:
        if item.get("kind") == "reading":
            unit = item.get("unit") or ""
            lines.append(
                f"读数 {item.get('metric')}={item.get('value')}{unit}"
                f"（{item.get('at') or '时间未知'}）"
            )
        elif item.get("kind") == "analysis":
            lines.append(f"设备分析发现 {item.get('code') or item.get('codes')}")
        elif item.get("kind") == "record":
            lines.append(f"农事记录 {item.get('record_id')}（{item.get('occurred_on')}）")
        elif item.get("kind") == "cycle":
            lines.append(f"预计采收 {item.get('expected_harvest_on')}")
        elif item.get("kind") == "forecast":
            lines.append(f"天气预报 {item.get('window')}")
    return "\n".join(lines)


def _create_alert(
    db: Session,
    *,
    yard_id: str,
    run: FarmAgentRun,
    action: FarmAgentAction,
    finding: dict[str, Any],
) -> FarmAlert:
    severity = agent_rules.ALERT_SEVERITY.get(action.severity, "normal")
    if severity not in ALERT_SEVERITIES:
        severity = "normal"
    payload = action.payload_json or {}
    detail = action.detail or finding.get("detail") or ""
    evidence_text = _evidence_lines(finding)
    if evidence_text:
        detail = f"{detail}\n依据：{evidence_text}"
    alert = FarmAlert(
        yard_id=yard_id,
        title=action.title[:160],
        detail=detail[:2000],
        severity=severity,
        source=ACTION_SOURCE,
        plot_id=payload.get("plot_id"),
        status="new",
        agent_run_id=run.id,
        created_by=None,
    )
    db.add(alert)
    db.flush()
    return alert


def _create_work_order(
    db: Session,
    *,
    yard_id: str,
    run: FarmAgentRun,
    action: FarmAgentAction,
    actor_id: str | None,
) -> WorkOrder:
    payload = action.payload_json or {}
    priority = str(
        payload.get("priority") or agent_rules.WORK_PRIORITY.get(action.severity, "normal")
    )
    if priority not in ("low", "normal", "high", "urgent"):
        priority = "normal"
    due_on = payload.get("due_on")
    try:
        due = date.fromisoformat(str(due_on)[:10]) if due_on else None
    except ValueError:
        due = None
    order = WorkOrder(
        yard_id=yard_id,
        title=action.title[:160],
        description=(action.detail or action.title)[:2000],
        priority=priority,
        status="open",
        source=ACTION_SOURCE,
        source_ref=action.id,
        plot_id=payload.get("plot_id"),
        due_on=due,
        agent_run_id=run.id,
        created_by=actor_id,
    )
    db.add(order)
    db.flush()
    db.add(
        WorkOrderEvent(
            yard_id=yard_id,
            work_order_id=order.id,
            kind="created",
            note="由 Agent 自动执行创建",
            actor_id=actor_id,
            to_status=order.status,
        )
    )
    return order


def _create_diagnosis(
    db: Session,
    *,
    yard_id: str,
    run: FarmAgentRun,
    action: FarmAgentAction,
    finding: dict[str, Any],
    actor_id: str | None,
) -> dict[str, Any]:
    from app.modules.farm import diagnosis_service
    from app.modules.farm.schemas import DiagnosisCreate

    payload = action.payload_json or {}
    crop = str(payload.get("crop") or "").strip()
    if not crop:
        raise AppError("FARM_AGENT_NO_CROP", "缺少作物信息，不能自动发起诊断。", http_status=422)
    symptoms = (
        f"Agent 自动执行：{finding.get('title')}。{finding.get('detail')}\n"
        f"建议：{finding.get('suggestion')}\n依据：{_evidence_lines(finding)}"
    )[:4000]
    req = DiagnosisCreate(
        crop=crop,
        symptoms=symptoms,
        plot_id=payload.get("plot_id"),
        environment={"source": "farm_agent", "run_id": run.id, "rule": finding.get("code")},
    )
    return diagnosis_service.create_diagnosis(
        db,
        yard_id=yard_id,
        user_id=actor_id or run.created_by or "system",
        req=req,
        source="agent",
        device_id=payload.get("device_id"),
    )


def _create_record(
    db: Session,
    *,
    yard_id: str,
    run: FarmAgentRun,
    action: FarmAgentAction,
    finding: dict[str, Any],
    actor_id: str | None,
) -> FarmRecord:
    payload = action.payload_json or {}
    plot_id = payload.get("plot_id")
    if not plot_id:
        raise AppError(
            "FARM_AGENT_NO_PLOT",
            "设备未绑定地块，无法把观察写成地块台账。",
            http_status=422,
        )
    kind = str(payload.get("kind") or "scouting")
    record = FarmRecord(
        yard_id=yard_id,
        plot_id=plot_id,
        kind=kind,
        occurred_on=(run.started_at or _utcnow()).date(),
        title=action.title[:120],
        content=(action.detail or finding.get("detail") or "")[:5000],
        operator_name="Agent",
        metadata_json={
            "agent_run_id": run.id,
            "rule": finding.get("code"),
            "evidence": finding.get("evidence"),
        },
        source=ACTION_SOURCE,
        agent_run_id=run.id,
        created_by=actor_id,
    )
    db.add(record)
    db.flush()
    return record


def _create_pesticide(
    db: Session,
    *,
    yard_id: str,
    run: FarmAgentRun,
    action: FarmAgentAction,
    actor_id: str | None,
) -> PesticideProduct:
    payload = action.payload_json or {}
    name = str(payload.get("name") or "").strip()
    if not name:
        raise AppError("FARM_AGENT_NO_NAME", "缺少药剂名称，不能建登记草稿。", http_status=422)
    product = PesticideProduct(
        yard_id=yard_id,
        name=name[:120],
        active_ingredient="",
        registered_crops=None,
        pre_harvest_interval_days=0,
        label_notes=(
            "由 Agent 从农事记录识别后建草稿：有效成分、登记作物与安全间隔期需人工补充后再启用；"
            "未补充前不参与安全间隔核算。"
        ),
        status="draft",
        created_by=actor_id,
    )
    try:
        with db.begin_nested():
            db.add(product)
            db.flush()
    except IntegrityError as e:
        raise AppError(
            "PESTICIDE_DUPLICATE", f"药剂「{name}」已在药剂库登记。", http_status=409
        ) from e
    return product


def _create_plot(
    db: Session,
    *,
    yard_id: str,
    action: FarmAgentAction,
    actor_id: str | None,
) -> FarmPlot:
    payload = action.payload_json or {}
    name = str(payload.get("name") or "").strip()
    if not name:
        raise AppError("FARM_AGENT_NO_NAME", "缺少地块名称，不能建档。", http_status=422)
    plot = FarmPlot(
        yard_id=yard_id,
        name=name[:80],
        crop=str(payload.get("crop") or "")[:60],
        status="active",
        notes=str(payload.get("notes") or "由 Agent 建议建档，请补充面积、位置与灌溉方式。")[:2000],
        created_by=actor_id,
    )
    try:
        with db.begin_nested():
            db.add(plot)
            db.flush()
    except IntegrityError as e:
        raise AppError("FARM_PLOT_DUPLICATE", f"地块「{name}」已存在。", http_status=409) from e
    return plot


def _push(
    db: Session, *, yard_id: str, event: str, payload: dict[str, Any], request_id: str
) -> None:
    try:
        from app.modules.push import service as push_service

        push_service.dispatch(
            db, yard_id=yard_id, event_type=event, payload=payload, request_id=request_id
        )
    except Exception as e:  # noqa: BLE001 —— 推送失败不影响主流程
        log.warning("farm agent push failed: %s", e)


def execute_action(
    db: Session,
    *,
    run: FarmAgentRun,
    action: FarmAgentAction,
    finding: dict[str, Any],
    actor_id: str | None,
    request_id: str,
) -> None:
    """真正执行一个动作；产物写回 target_type/target_id，失败如实记录。"""
    try:
        target_type: str | None = None
        target_id: str | None = None
        payload = action.payload_json or {}
        if action.kind == "alert":
            alert = _create_alert(db, yard_id=run.yard_id, run=run, action=action, finding=finding)
            target_type, target_id = "farm_alert", alert.id
            _push(
                db,
                yard_id=run.yard_id,
                event="farm_alert",
                payload={
                    "title": alert.title,
                    "alert_id": alert.id,
                    "severity": alert.severity,
                    "rule": action.rule_code,
                    "detail": alert.detail[:300],
                    "plot_id": alert.plot_id,
                    "device_id": payload.get("device_id"),
                    "run_id": run.id,
                },
                request_id=request_id,
            )
        elif action.kind == "work_order":
            order = _create_work_order(
                db, yard_id=run.yard_id, run=run, action=action, actor_id=actor_id
            )
            target_type, target_id = "work_order", order.id
            _push(
                db,
                yard_id=run.yard_id,
                event="workorder_created",
                payload={
                    "title": order.title,
                    "work_order_id": order.id,
                    "priority": order.priority,
                    "plot_id": order.plot_id,
                    "source": order.source,
                    "run_id": run.id,
                },
                request_id=request_id,
            )
        elif action.kind == "diagnosis":
            row = _create_diagnosis(
                db,
                yard_id=run.yard_id,
                run=run,
                action=action,
                finding=finding,
                actor_id=actor_id,
            )
            target_type, target_id = "crop_diagnosis", str(row.get("diagnosis_id"))
        elif action.kind == "record":
            record = _create_record(
                db,
                yard_id=run.yard_id,
                run=run,
                action=action,
                finding=finding,
                actor_id=actor_id,
            )
            target_type, target_id = "farm_record", record.id
        elif action.kind == "pesticide":
            product = _create_pesticide(
                db, yard_id=run.yard_id, run=run, action=action, actor_id=actor_id
            )
            target_type, target_id = "pesticide_product", product.id
        elif action.kind == "plot":
            plot = _create_plot(db, yard_id=run.yard_id, action=action, actor_id=actor_id)
            target_type, target_id = "farm_plot", plot.id
        else:
            raise AppError("FARM_AGENT_UNKNOWN_ACTION", f"未知动作类型：{action.kind}")
        action.target_type = target_type
        action.target_id = target_id
        action.status = "executed"
        action.error = None
    except AppError as e:
        action.status = "failed"
        action.error = f"{e.code}: {e.message}"[:300]
    except Exception as e:  # noqa: BLE001 —— 单动作失败不影响其余动作
        log.warning("farm agent action failed: %s", e)
        action.status = "failed"
        action.error = str(e)[:300]
    db.flush()


# ---------------------------------------------------------------------------
# 运行一次
# ---------------------------------------------------------------------------


def _recent_dedupe_keys(db: Session, yard_id: str, since: datetime) -> set[str]:
    rows = db.execute(
        select(FarmAgentAction.dedupe_key).where(
            FarmAgentAction.yard_id == yard_id,
            FarmAgentAction.created_at >= since,
            FarmAgentAction.status != "dismissed",
        )
    ).all()
    return {str(row[0]) for row in rows if row[0]}


def run_agent(
    db: Session,
    *,
    yard_id: str,
    actor_id: str | None,
    request_id: str = "",
    trigger: str = "manual",
    now: datetime | None = None,
) -> dict[str, Any]:
    """跑一次自动执行。定时任务在未启用时直接返回 ``skipped``。"""
    setting = get_setting(db, yard_id, "farm_agent")
    if trigger != "manual" and not setting.get("enabled"):
        return {"status": "skipped", "reason": "未启用 Agent 自动执行"}

    now = _as_utc(now) or _utcnow()
    try:
        window_hours = int(setting.get("window_hours") or 24)
    except (TypeError, ValueError):
        window_hours = 24

    run = FarmAgentRun(
        yard_id=yard_id,
        trigger=trigger,
        status="running",
        level="info",
        window_from=now - timedelta(hours=window_hours),
        window_to=now,
        summary="",
        started_at=now,
        created_by=actor_id,
    )
    db.add(run)
    db.flush()
    run_id = run.id
    # 先落运行记录：后续任一步失败都还能留下可追溯的失败记录
    db.commit()

    try:
        evidence = agent_evidence.collect(db, yard_id, now=now, window_hours=window_hours)
        findings = agent_rules.evaluate(
            evidence,
            setting.get("thresholds") or {},
            now=now,
            today=now.date(),
        )[:MAX_FINDINGS]
        level = agent_rules.overall_level(findings)

        device_count = len(evidence.get("devices") or [])
        has_forecast = bool((evidence.get("weather") or {}).get("forecast"))
        status = "ok" if (device_count or has_forecast) else "insufficient"
        summary = agent_rules.rule_summary(findings, device_count=device_count)
        if evidence.get("warnings"):
            summary = summary + "\n提示：" + "；".join(evidence["warnings"][:4])

        model: str | None = None
        tokens = 0
        if (
            findings
            and bool(setting.get("explain_with_llm"))
            and int(setting.get("daily_budget_tokens") or 0) > _budget_used_today(db, yard_id, now)
        ):
            llm_summary, model, tokens = explain_with_llm(
                db,
                yard_id=yard_id,
                findings=findings,
                evidence=evidence,
                actor_id=actor_id,
                request_id=request_id,
            )
            if llm_summary:
                summary = llm_summary

        run.evidence_json = agent_evidence.summarize(evidence)
        run.findings_json = findings
        run.summary = summary[:SUMMARY_CHARS]
        run.model = model
        run.tokens_used = tokens
        run.level = level
        run.status = status
        db.flush()

        dedupe_since = now - timedelta(hours=_dedupe_hours(setting))
        seen = _recent_dedupe_keys(db, yard_id, dedupe_since)
        created = executed = pending = skipped = deduped = 0
        pending_actions: list[FarmAgentAction] = []
        executed_actions: list[FarmAgentAction] = []
        for finding in findings:
            for item in finding.get("actions") or []:
                if created >= MAX_ACTIONS_PER_RUN:
                    break
                kind = str(item.get("kind") or "")
                if kind not in FARM_AGENT_ACTION_KINDS:
                    continue
                policy = policy_for(setting, kind)
                if policy == "off":
                    skipped += 1
                    continue
                dedupe_key = f"{finding.get('dedupe_key')}:{kind}"
                if dedupe_key in seen:
                    deduped += 1
                    continue
                seen.add(dedupe_key)
                action = FarmAgentAction(
                    yard_id=yard_id,
                    run_id=run.id,
                    kind=kind,
                    rule_code=str(finding.get("code") or ""),
                    status="pending" if policy == "confirm" else "executed",
                    policy=policy,
                    severity=str(finding.get("level") or "info"),
                    title=str(item.get("title") or finding.get("title") or "")[:160],
                    detail=str(item.get("detail") or finding.get("detail") or "")[:2000],
                    payload_json=item.get("payload") or {},
                    dedupe_key=dedupe_key,
                )
                db.add(action)
                db.flush()
                created += 1
                if policy == "confirm":
                    pending += 1
                    pending_actions.append(action)
                else:
                    execute_action(
                        db,
                        run=run,
                        action=action,
                        finding=finding,
                        actor_id=actor_id,
                        request_id=request_id,
                    )
                    if action.status == "executed":
                        executed += 1
                        executed_actions.append(action)

        if executed_actions or pending_actions:
            _push(
                db,
                yard_id=yard_id,
                event="farm_agent_run",
                payload={
                    "title": f"Agent 自动执行：{len(findings)} 条发现",
                    "run_id": run.id,
                    "level": level,
                    "findings": len(findings),
                    "executed": executed,
                    "pending": pending,
                    "summary": summary[:400],
                },
                request_id=request_id,
            )

        finished = _utcnow()
        run.finished_at = finished
        run.duration_ms = int((finished - now).total_seconds() * 1000)
        if deduped:
            run.summary = (
                run.summary + f"\n另有 {deduped} 个动作在降噪窗口内已产出，本轮不重复。"
            )[:SUMMARY_CHARS]
        log_audit(
            db,
            yard_id=yard_id,
            actor_id=actor_id,
            action="farm_agent_run",
            resource_type="farm_agent_run",
            resource_id=run.id,
            result="success" if status == "ok" else status,
            request_id=request_id,
            metadata={
                "trigger": trigger,
                "findings": len(findings),
                "actions": created,
                "executed": executed,
                "pending": pending,
                "skipped": skipped,
                "deduped": deduped,
                "level": level,
                "devices": device_count,
            },
        )
        db.commit()
        return run_out(run, executed_actions + pending_actions)
    except Exception as e:  # noqa: BLE001 —— 运行失败要留痕，不把异常丢给定时器
        db.rollback()
        log.warning("farm agent run failed: %s", e)
        row = db.get(FarmAgentRun, run_id)
        if row is not None:
            row.status = "failed"
            row.error = str(getattr(e, "message", None) or e)[:300]
            row.finished_at = _utcnow()
            row.summary = row.summary or "本次自动执行失败，详见错误信息。"
            db.commit()
        raise


# ---------------------------------------------------------------------------
# 待确认动作
# ---------------------------------------------------------------------------


def _get_action(db: Session, yard_id: str, action_id: str) -> FarmAgentAction:
    row = db.scalar(
        select(FarmAgentAction).where(
            FarmAgentAction.yard_id == yard_id, FarmAgentAction.id == action_id
        )
    )
    if row is None:
        raise AppError("FARM_AGENT_ACTION_NOT_FOUND", "动作不存在或无权访问。", http_status=404)
    return row


def _finding_for(db: Session, action: FarmAgentAction) -> dict[str, Any]:
    run = db.get(FarmAgentRun, action.run_id)
    for item in (run.findings_json or []) if run else []:
        if str(item.get("code")) == action.rule_code:
            return item
    return {
        "code": action.rule_code,
        "title": action.title,
        "detail": action.detail,
        "evidence": [],
    }


def confirm_action(
    db: Session, yard_id: str, action_id: str, actor_id: str, request_id: str = ""
) -> dict[str, Any]:
    action = _get_action(db, yard_id, action_id)
    if action.status != "pending":
        raise AppError(
            "FARM_AGENT_ACTION_NOT_PENDING",
            "只有待确认的动作可以执行。",
            http_status=409,
        )
    run = db.get(FarmAgentRun, action.run_id)
    if run is None:
        raise AppError("FARM_AGENT_RUN_NOT_FOUND", "动作所属运行记录不存在。", http_status=404)
    action.confirmed_by = actor_id
    action.confirmed_at = _utcnow()
    execute_action(
        db,
        run=run,
        action=action,
        finding=_finding_for(db, action),
        actor_id=actor_id,
        request_id=request_id,
    )
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=actor_id,
        action="farm_agent_action_confirmed",
        resource_type="farm_agent_action",
        resource_id=action.id,
        result="success" if action.status == "executed" else "failed",
        request_id=request_id,
        metadata={"kind": action.kind, "target": action.target_type},
    )
    db.commit()
    return action_out(action)


def dismiss_action(db: Session, yard_id: str, action_id: str, actor_id: str) -> dict[str, Any]:
    action = _get_action(db, yard_id, action_id)
    if action.status != "pending":
        raise AppError(
            "FARM_AGENT_ACTION_NOT_PENDING",
            "只有待确认的动作可以忽略。",
            http_status=409,
        )
    action.status = "dismissed"
    action.confirmed_by = actor_id
    action.confirmed_at = _utcnow()
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=actor_id,
        action="farm_agent_action_dismissed",
        resource_type="farm_agent_action",
        resource_id=action.id,
        result="success",
        request_id="",
        metadata={"kind": action.kind},
    )
    db.commit()
    return action_out(action)


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


def pending_actions(db: Session, yard_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(FarmAgentAction)
        .where(FarmAgentAction.yard_id == yard_id, FarmAgentAction.status == "pending")
        .order_by(FarmAgentAction.created_at.desc())
        .limit(limit)
    ).all()
    return [action_out(row) for row in rows]


def overview(db: Session, yard_id: str) -> dict[str, Any]:
    setting = get_setting(db, yard_id, "farm_agent")
    last = db.scalar(
        select(FarmAgentRun)
        .where(FarmAgentRun.yard_id == yard_id)
        .order_by(FarmAgentRun.created_at.desc())
        .limit(1)
    )
    pending_count = int(
        db.scalar(
            select(func.count(FarmAgentAction.id)).where(
                FarmAgentAction.yard_id == yard_id,
                FarmAgentAction.status == "pending",
            )
        )
        or 0
    )
    today_start = datetime.combine(_utcnow().date(), datetime.min.time(), tzinfo=UTC)
    today_executed = int(
        db.scalar(
            select(func.count(FarmAgentAction.id)).where(
                FarmAgentAction.yard_id == yard_id,
                FarmAgentAction.status == "executed",
                FarmAgentAction.created_at >= today_start,
            )
        )
        or 0
    )
    return {
        "enabled": bool(setting.get("enabled")),
        "schedule": setting.get("schedule") or {"preset": "daily"},
        "actions": setting.get("actions") or {},
        "weather_provider": setting.get("weather_provider") or "none",
        "weather_configured": bool(setting.get("weather_api_key"))
        and (setting.get("weather_provider") or "none") != "none",
        "dedupe_hours": _dedupe_hours(setting),
        "last_run": run_out(last) if last is not None else None,
        "pending_count": pending_count,
        "pending": pending_actions(db, yard_id, limit=20),
        "today_executed": today_executed,
    }


def list_runs(
    db: Session, yard_id: str, *, page: int = 1, page_size: int = 20
) -> tuple[list[dict[str, Any]], int]:
    q = select(FarmAgentRun).where(FarmAgentRun.yard_id == yard_id)
    total = int(db.scalar(select(func.count()).select_from(q.order_by(None).subquery())) or 0)
    rows = db.scalars(
        q.order_by(FarmAgentRun.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return [run_out(row) for row in rows], total


def get_run(db: Session, yard_id: str, run_id: str) -> dict[str, Any]:
    row = db.scalar(
        select(FarmAgentRun).where(FarmAgentRun.yard_id == yard_id, FarmAgentRun.id == run_id)
    )
    if row is None:
        raise AppError("FARM_AGENT_RUN_NOT_FOUND", "运行记录不存在或无权访问。", http_status=404)
    actions = list(
        db.scalars(
            select(FarmAgentAction)
            .where(FarmAgentAction.yard_id == yard_id, FarmAgentAction.run_id == run_id)
            .order_by(FarmAgentAction.created_at.asc())
        )
    )
    return run_out(row, actions)


def list_actions(
    db: Session,
    yard_id: str,
    *,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    q = select(FarmAgentAction).where(FarmAgentAction.yard_id == yard_id)
    if status:
        q = q.where(FarmAgentAction.status == status)
    total = int(db.scalar(select(func.count()).select_from(q.order_by(None).subquery())) or 0)
    rows = db.scalars(
        q.order_by(FarmAgentAction.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return [action_out(row) for row in rows], total


def latest_run_for_job(db: Session, yard_id: str) -> FarmAgentRun | None:
    return db.scalar(
        select(FarmAgentRun)
        .where(FarmAgentRun.yard_id == yard_id)
        .order_by(FarmAgentRun.created_at.desc())
        .limit(1)
    )


__all__ = [
    "JOB_KIND",
    "action_out",
    "confirm_action",
    "dismiss_action",
    "get_run",
    "list_actions",
    "list_runs",
    "overview",
    "policy_for",
    "run_agent",
    "run_out",
]
