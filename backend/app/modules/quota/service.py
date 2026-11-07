"""分级额度服务（v3.32）：策略读写、概览、环境模板、申请审批。"""

from __future__ import annotations

import math
from typing import Any

from app.core.errors import AppError
from app.modules.agents.models import STATUS_DELETED, YardAgent
from app.modules.llm.supplier_models import Supplier
from app.modules.quota import engine, environments
from app.modules.quota.models import (
    METRIC_LABELS,
    ON_EXCEED_LABELS,
    SCOPE_LABELS,
    SOURCE_TEMPLATE,
    STATUS_PENDING,
    WINDOW_LABELS,
    QuotaPolicy,
    QuotaRequest,
)
from app.modules.yards.settings_service import get_setting, update_setting
from sqlalchemy import func, select
from sqlalchemy.orm import Session

SETTINGS_KEY = "quota"


def _policy_payload(db: Session, policy: QuotaPolicy) -> dict[str, Any]:
    used, start = engine.current_usage(db, policy)
    limit = engine.effective_limit(db, policy)
    # 无限制（limit_value<0）→ effective_limit 为 inf，序列化转 null 并打标，
    # 与「零余额」（limit==0，拒绝服务）严格区分（审查 B3）。
    unlimited = not math.isfinite(limit)
    # 临时额度按剩余余额展示（已跨窗口扣减），不能再用 effective_limit-limit 反推（审查 B4）
    grants = 0.0 if unlimited else engine.grant_total(db, policy.id)
    percent = 0.0 if unlimited or limit <= 0 else round(used / limit * 100, 2)
    return {
        "id": policy.id,
        "scope_type": policy.scope_type,
        "scope_type_label": SCOPE_LABELS.get(policy.scope_type, policy.scope_type),
        "scope_key": policy.scope_key,
        "metric": policy.metric,
        "metric_label": METRIC_LABELS.get(policy.metric, policy.metric),
        "window": policy.window,
        "window_label": WINDOW_LABELS.get(policy.window, policy.window),
        "limit_value": policy.limit_value,
        "unlimited": unlimited,
        "granted": round(grants, 4),
        "effective_limit": None if unlimited else round(limit, 4),
        "used": round(used, 4),
        "remaining": None if unlimited else round(max(limit - used, 0.0), 4),
        "usage_percent": percent,
        "on_exceed": policy.on_exceed,
        "on_exceed_label": ON_EXCEED_LABELS.get(policy.on_exceed, policy.on_exceed),
        "enabled": policy.enabled,
        "source": policy.source,
        "note": policy.note,
        "window_start": start,
    }


def _request_payload(row: QuotaRequest) -> dict[str, Any]:
    return {
        "id": row.id,
        "policy_id": row.policy_id,
        "scope_type": row.scope_type,
        "scope_type_label": SCOPE_LABELS.get(row.scope_type, row.scope_type),
        "scope_key": row.scope_key,
        "metric": row.metric,
        "metric_label": METRIC_LABELS.get(row.metric, row.metric),
        "window": row.window,
        "window_label": WINDOW_LABELS.get(row.window, row.window),
        "current_limit": row.current_limit,
        "requested_limit": row.requested_limit,
        "reason": row.reason,
        "status": row.status,
        "requester_id": row.requester_id,
        "requester_name": row.requester_name,
        "decided_by": row.decided_by,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "decision_note": row.decision_note,
        "valid_days": row.valid_days,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _agent_options(db: Session, yard_id: str) -> list[dict[str, Any]]:
    rows = db.execute(
        select(YardAgent.agent_code, YardAgent.name, YardAgent.group_name)
        .where(YardAgent.yard_id == yard_id, YardAgent.status != STATUS_DELETED)
        .order_by(YardAgent.created_at.desc())
    ).all()
    seen: dict[str, dict[str, Any]] = {}
    for code, name, group in rows:
        if code in seen:
            continue
        seen[str(code)] = {"code": str(code), "name": str(name), "group": group or ""}
    return list(seen.values())


def _model_options(db: Session, yard_id: str) -> list[str]:
    rows = db.execute(
        select(Supplier.model_id).where(
            (Supplier.yard_id.is_(None)) | (Supplier.yard_id == yard_id)
        )
    ).all()
    return sorted({str(r[0]) for r in rows if r and r[0]})


def _ensure_environment_policies(db: Session, yard_id: str, environment: str) -> None:
    """首次打开额度页时按当前环境补齐模板策略（幂等）。

    没有这一步，小院会处于「设置了环境但一条策略都没有」的悬空状态，
    Token 额度看似接管了、其实完全没有生效。
    """
    exists = db.scalar(select(QuotaPolicy.id).where(QuotaPolicy.yard_id == yard_id).limit(1))
    if exists is not None:
        return
    for item in environments.template_policies(environment):
        db.add(QuotaPolicy(yard_id=yard_id, created_by=None, **item))
    db.commit()


def overview(db: Session, yard_id: str) -> dict[str, Any]:
    settings = get_setting(db, yard_id, SETTINGS_KEY)
    _ensure_environment_policies(db, yard_id, str(settings.get("environment") or "standard"))
    policies = list(
        db.scalars(
            select(QuotaPolicy)
            .where(QuotaPolicy.yard_id == yard_id)
            .order_by(QuotaPolicy.scope_type, QuotaPolicy.scope_key)
        )
    )
    pending = db.execute(
        select(func.count(QuotaRequest.id)).where(
            QuotaRequest.yard_id == yard_id, QuotaRequest.status == STATUS_PENDING
        )
    ).scalar_one()
    return {
        "environment": settings.get("environment"),
        "environments": environments.payload(),
        "legacy_calls_mode": settings.get("legacy_calls_mode"),
        "default_grant_days": settings.get("default_grant_days"),
        "policies": [_policy_payload(db, p) for p in policies],
        "pending_requests": int(pending or 0),
        "options": {
            "agents": _agent_options(db, yard_id),
            "models": _model_options(db, yard_id),
        },
    }


def upsert_policy(
    db: Session,
    *,
    yard_id: str,
    actor_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    scope_key = str(payload.get("scope_key") or "").strip()
    scope_type = payload["scope_type"]
    if scope_type == "yard":
        scope_key = ""
    elif not scope_key:
        raise AppError("INVALID_REQUEST", "请选择这条额度作用的对象。", http_status=400)
    if scope_type == "agent":
        exists = db.scalar(
            select(YardAgent.id).where(
                YardAgent.yard_id == yard_id,
                YardAgent.agent_code == scope_key,
                YardAgent.status != STATUS_DELETED,
            )
        )
        if exists is None:
            raise AppError("AGENT_NOT_FOUND", "该 Agent 不存在。", http_status=404)
    row = db.scalar(
        select(QuotaPolicy).where(
            QuotaPolicy.yard_id == yard_id,
            QuotaPolicy.scope_type == scope_type,
            QuotaPolicy.scope_key == scope_key,
            QuotaPolicy.metric == payload["metric"],
            QuotaPolicy.window == payload["window"],
        )
    )
    if row is None:
        row = QuotaPolicy(
            yard_id=yard_id,
            scope_type=scope_type,
            scope_key=scope_key,
            metric=payload["metric"],
            window=payload["window"],
            created_by=actor_id,
        )
        db.add(row)
    row.limit_value = float(payload["limit_value"])
    row.on_exceed = payload["on_exceed"]
    row.enabled = bool(payload.get("enabled", True))
    row.note = payload.get("note")
    # 人工改过的策略不再被环境模板覆盖
    row.source = "manual"
    db.commit()
    return _policy_payload(db, row)


def delete_policy(db: Session, *, yard_id: str, policy_id: str) -> None:
    row = db.get(QuotaPolicy, policy_id)
    if row is None or row.yard_id != yard_id:
        raise AppError("QUOTA_POLICY_NOT_FOUND", "额度策略不存在。", http_status=404)
    db.delete(row)
    db.commit()


def apply_environment(
    db: Session, *, yard_id: str, environment: str, actor_id: str
) -> dict[str, Any]:
    """切换环境：只覆盖模板来源的策略，保留人工配置。"""
    if environment not in environments.ENVIRONMENTS:
        raise AppError("INVALID_REQUEST", "未知的额度环境。", http_status=400)
    db.query(QuotaPolicy).filter(
        QuotaPolicy.yard_id == yard_id, QuotaPolicy.source == SOURCE_TEMPLATE
    ).delete(synchronize_session=False)
    for item in environments.template_policies(environment):
        db.add(QuotaPolicy(yard_id=yard_id, created_by=actor_id, **item))
    db.commit()
    update_setting(
        db,
        yard_id=yard_id,
        key=SETTINGS_KEY,
        value={"environment": environment},
        actor_id=actor_id,
        request_id="quota-environment",
    )
    return overview(db, yard_id)


def list_requests(
    db: Session, *, yard_id: str, status: str | None = None, requester_id: str | None = None
) -> list[dict[str, Any]]:
    stmt = select(QuotaRequest).where(QuotaRequest.yard_id == yard_id)
    if status:
        stmt = stmt.where(QuotaRequest.status == status)
    if requester_id:
        stmt = stmt.where(QuotaRequest.requester_id == requester_id)
    rows = db.scalars(stmt.order_by(QuotaRequest.created_at.desc()).limit(200))
    return [_request_payload(r) for r in rows]


def get_request(db: Session, *, yard_id: str, request_id: str) -> QuotaRequest:
    row = db.get(QuotaRequest, request_id)
    if row is None or row.yard_id != yard_id:
        raise AppError("QUOTA_REQUEST_NOT_FOUND", "申请单不存在。", http_status=404)
    return row
