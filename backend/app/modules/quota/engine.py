"""额度引擎（v3.32）：策略解析、调用前预检、调用后记账、临时额度。

一次模型调用会命中多层策略，检查顺序按「越具体越先」：小院 → Agent 分组 →
单个 Agent → 供应商 → 模型。任一层触发拦截就整体拒绝，不做「上层放过下层再拦」
的半吊子处理，避免出现在 A 层扣了、B 层没扣的错账。

Token / 金额是后付费指标（调用前不知道要花多少），因此预检只判断「是否已经用尽」，
真正的累加在拿到响应后由 ``charge()`` 完成；调用次数可前付费，预检直接按 +1 判断。
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.errors import AppError
from app.core.notify import send_alert
from app.modules.quota.models import (
    METRIC_CALLS,
    ON_EXCEED_APPROVE,
    ON_EXCEED_BLOCK,
    ON_EXCEED_WARN,
    SCOPE_AGENT,
    SCOPE_AGENT_GROUP,
    SCOPE_MODEL,
    SCOPE_SUPPLIER,
    SCOPE_YARD,
    STATUS_APPROVED,
    STATUS_PENDING,
    QuotaCounter,
    QuotaGrant,
    QuotaPolicy,
    QuotaRequest,
)
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

WARN_RATIO = 0.8
#: 同一策略同一窗口的告警去重档位（80% / 100%）
_ALERT_KEYS = (80, 100)


def window_start(window: str, now: datetime | None = None) -> str:
    moment = now or datetime.now(UTC)
    if window == "day":
        return moment.strftime("%Y-%m-%d")
    return moment.strftime("%Y-%m")


def _window_end(window: str, now: datetime | None = None) -> datetime:
    """当前窗口结束时刻（临时额度过期时间兜底用）。"""
    moment = now or datetime.now(UTC)
    if window == "day":
        start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        return start + timedelta(days=1)
    first = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (first + timedelta(days=32)).replace(day=1)


def agent_group(db: Session, yard_id: str, agent_code: str | None) -> str:
    """Agent 分组：取该编码最新一条非删除记录上的 group_name。"""
    if not agent_code:
        return ""
    from app.modules.agents.models import STATUS_DELETED, YardAgent

    row = db.execute(
        select(YardAgent.group_name)
        .where(
            YardAgent.yard_id == yard_id,
            YardAgent.agent_code == agent_code,
            YardAgent.status != STATUS_DELETED,
        )
        .order_by(YardAgent.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return str(row or "").strip()


def matching_policies(
    db: Session,
    yard_id: str,
    *,
    agent_code: str | None = None,
    supplier_id: str | None = None,
    model: str | None = None,
    now: datetime | None = None,
) -> list[tuple[QuotaPolicy, str]]:
    """返回 [(策略, 该层作用域键)]，按从宽到窄排序。"""
    rows = list(
        db.scalars(
            select(QuotaPolicy).where(QuotaPolicy.yard_id == yard_id, QuotaPolicy.enabled.is_(True))
        )
    )
    if not rows:
        return []
    group = agent_group(db, yard_id, agent_code)
    matched: list[tuple[QuotaPolicy, str]] = []
    for policy in rows:
        if policy.scope_type == SCOPE_YARD:
            matched.append((policy, ""))
        elif policy.scope_type == SCOPE_AGENT_GROUP and group and policy.scope_key == group:
            matched.append((policy, group))
        elif policy.scope_type == SCOPE_AGENT and agent_code and policy.scope_key == agent_code:
            matched.append((policy, agent_code))
        elif (
            policy.scope_type == SCOPE_SUPPLIER and supplier_id and policy.scope_key == supplier_id
        ):
            matched.append((policy, supplier_id))
        elif policy.scope_type == SCOPE_MODEL and model and policy.scope_key == model:
            matched.append((policy, model))
    order = {SCOPE_YARD: 0, SCOPE_AGENT_GROUP: 1, SCOPE_AGENT: 2, SCOPE_SUPPLIER: 3, SCOPE_MODEL: 4}
    matched.sort(key=lambda item: order.get(item[0].scope_type, 9))
    return matched


def grant_total(db: Session, policy_id: str, now: datetime | None = None) -> float:
    moment = now or datetime.now(UTC)
    total = db.execute(
        select(func.coalesce(func.sum(QuotaGrant.amount), 0.0)).where(
            QuotaGrant.policy_id == policy_id, QuotaGrant.expires_at > moment
        )
    ).scalar_one()
    return float(total or 0.0)


def is_unlimited(policy: QuotaPolicy) -> bool:
    """显式「无限制」哨兵：limit_value < 0（审查 B3）。

    与「零余额」严格区分——limit_value == 0 且无有效临时额度时是零余额，
    必须按 on_exceed 拒绝/转申请，绝不放行。
    """
    return float(policy.limit_value) < 0


def effective_limit(db: Session, policy: QuotaPolicy, now: datetime | None = None) -> float:
    """有效上限 = 基线上限 + 未到期临时额度。

    无限制策略返回 ``math.inf``（调用方据此跳过拦截与告警，序列化时转 null）；
    零余额策略返回 0，由 preflight 当作「已用尽」处理（审查 B3）。
    """
    if is_unlimited(policy):
        return math.inf
    return float(policy.limit_value) + grant_total(db, policy.id, now)


def current_usage(
    db: Session, policy: QuotaPolicy, *, now: datetime | None = None
) -> tuple[float, str]:
    used = db.execute(
        select(QuotaCounter.used).where(
            QuotaCounter.yard_id == policy.yard_id,
            QuotaCounter.scope_type == policy.scope_type,
            QuotaCounter.scope_key == policy.scope_key,
            QuotaCounter.metric == policy.metric,
            QuotaCounter.window == policy.window,
            QuotaCounter.window_start == window_start(policy.window, now),
        )
    ).scalar_one_or_none()
    return float(used or 0.0), window_start(policy.window, now)


def _bump(
    db: Session,
    *,
    yard_id: str,
    scope_type: str,
    scope_key: str,
    metric: str,
    window: str,
    amount: float,
    now: datetime | None = None,
) -> float:
    """累加用量（跨 SQLite/Postgres 的可移植写法：先 UPDATE，未命中再 INSERT）。"""
    start = window_start(window, now)
    stmt = (
        update(QuotaCounter)
        .where(
            QuotaCounter.yard_id == yard_id,
            QuotaCounter.scope_type == scope_type,
            QuotaCounter.scope_key == scope_key,
            QuotaCounter.metric == metric,
            QuotaCounter.window == window,
            QuotaCounter.window_start == start,
        )
        .values(used=QuotaCounter.used + amount)
        .execution_options(synchronize_session=False)
    )
    result = db.execute(stmt)
    if not getattr(result, "rowcount", 0):
        nested = db.begin_nested()
        try:
            db.add(
                QuotaCounter(
                    yard_id=yard_id,
                    scope_type=scope_type,
                    scope_key=scope_key,
                    metric=metric,
                    window=window,
                    window_start=start,
                    used=amount,
                )
            )
            nested.commit()
        except IntegrityError:
            # 并发下别人先插了同一行：回滚到保存点后重试累加
            nested.rollback()
            db.execute(stmt)
    used = db.execute(
        select(QuotaCounter.used).where(
            QuotaCounter.yard_id == yard_id,
            QuotaCounter.scope_type == scope_type,
            QuotaCounter.scope_key == scope_key,
            QuotaCounter.metric == metric,
            QuotaCounter.window == window,
            QuotaCounter.window_start == start,
        )
    ).scalar_one_or_none()
    return float(used or 0.0)


def _policy_label(policy: QuotaPolicy) -> str:
    from app.modules.quota.models import METRIC_LABELS, SCOPE_LABELS

    scope = SCOPE_LABELS.get(policy.scope_type, policy.scope_type)
    if policy.scope_key:
        scope = f"{scope}「{policy.scope_key}」"
    return f"{scope}的{METRIC_LABELS.get(policy.metric, policy.metric)}额度"


def _alert(policy: QuotaPolicy, used: float, limit: float, ratio: int) -> None:
    if ratio == 100:
        send_alert(
            title="quota",
            message=f"{_policy_label(policy)}已用尽（{used:.0f}/{limit:.0f}）。",
            level="critical",
            dedupe_key=f"quota_policy:{policy.id}:{window_start(policy.window)}:100",
        )
    else:
        send_alert(
            title="quota",
            message=f"{_policy_label(policy)}已用 {ratio}%（{used:.0f}/{limit:.0f}）。",
            level="warning",
            dedupe_key=f"quota_policy:{policy.id}:{window_start(policy.window)}:{ratio}",
        )


def _find_pending(db: Session, policy_id: str, requester_id: str) -> QuotaRequest | None:
    return db.scalar(
        select(QuotaRequest)
        .where(
            QuotaRequest.policy_id == policy_id,
            QuotaRequest.requester_id == requester_id,
            QuotaRequest.status == STATUS_PENDING,
        )
        .order_by(QuotaRequest.created_at.desc())
    )


def _open_request(
    db: Session,
    policy: QuotaPolicy,
    *,
    requester_id: str | None,
    requester_name: str | None,
    current_limit: float,
    reason: str = "",
) -> QuotaRequest:
    if requester_id:
        existing = _find_pending(db, policy.id, requester_id)
        if existing is not None:
            return existing
    row = QuotaRequest(
        yard_id=policy.yard_id,
        policy_id=policy.id,
        scope_type=policy.scope_type,
        scope_key=policy.scope_key,
        metric=policy.metric,
        window=policy.window,
        current_limit=current_limit,
        # 默认给到当前上限的 2 倍，审批人可改；不改也是合理的最小提额
        requested_limit=max(current_limit * 2, current_limit + 1),
        reason=reason or "额度已用尽，自动生成的提额申请。",
        requester_id=requester_id or "system",
        requester_name=requester_name,
    )
    db.add(row)
    db.flush()
    return row


def _raise_exceeded(
    db: Session,
    policy: QuotaPolicy,
    *,
    used: float,
    limit: float,
    request: QuotaRequest | None,
) -> None:
    label = _policy_label(policy)
    details: dict[str, Any] = {
        "policy_id": policy.id,
        "scope_type": policy.scope_type,
        "scope_key": policy.scope_key,
        "metric": policy.metric,
        "window": policy.window,
        "used": used,
        "limit": limit,
        "on_exceed": policy.on_exceed,
        "requestable": policy.on_exceed != ON_EXCEED_BLOCK,
        "request_id": request.id if request else None,
    }
    hint = ""
    if policy.on_exceed == ON_EXCEED_APPROVE:
        hint = "可在提示里直接提交提额申请，审批通过后自动生效。"
    elif policy.on_exceed == ON_EXCEED_WARN:
        hint = ""
    else:
        hint = "请联系小院管理员调整额度。"
    raise AppError(
        "QUOTA_EXCEEDED",
        f"{label}已用尽（{used:.0f}/{limit:.0f}）。{hint}",
        http_status=429,
        details=details,
    )


def preflight(
    db: Session,
    yard_id: str,
    *,
    agent_code: str | None = None,
    supplier_id: str | None = None,
    model: str | None = None,
    requester_id: str | None = None,
    requester_name: str | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """调用前检查所有命中策略；warn 只记录，block/approve 抛 429。"""
    warnings: list[dict[str, Any]] = []
    for policy, _scope_key in matching_policies(
        db, yard_id, agent_code=agent_code, supplier_id=supplier_id, model=model, now=now
    ):
        # 显式无限制（limit_value<0）直接放行；零余额（effective_limit==0）不再
        # 跳过，而是按「已用尽」走 on_exceed 拒绝/转申请（审查 B3：到期失败关闭，
        # 不再把零余额误当无限制放行）。
        if is_unlimited(policy):
            continue
        limit = effective_limit(db, policy, now)
        used, _start = current_usage(db, policy, now=now)
        # 调用次数是前付费（这次要占 1 次）；Token / 金额是后付费，用满即止
        allowed = used + 1 <= limit if policy.metric == METRIC_CALLS else used < limit
        if allowed:
            if used >= limit * WARN_RATIO:
                _alert(policy, used, limit, 80)
            continue
        if policy.on_exceed == ON_EXCEED_WARN:
            warnings.append(
                {
                    "policy_id": policy.id,
                    "scope_type": policy.scope_type,
                    "scope_key": policy.scope_key,
                    "metric": policy.metric,
                    "used": used,
                    "limit": limit,
                }
            )
            _alert(policy, used, limit, 100)
            continue
        request = None
        if policy.on_exceed == ON_EXCEED_APPROVE:
            request = _open_request(
                db,
                policy,
                requester_id=requester_id,
                requester_name=requester_name,
                current_limit=policy.limit_value,
            )
            db.commit()
        _alert(policy, used, limit, 100)
        _raise_exceeded(db, policy, used=used, limit=limit, request=request)
    return warnings


def charge(
    db: Session,
    yard_id: str,
    *,
    agent_code: str | None = None,
    supplier_id: str | None = None,
    model: str | None = None,
    tokens: float = 0,
    cost: float = 0,
    calls: float = 0,
    now: datetime | None = None,
) -> None:
    """调用后按实际用量累加；跨过 80%/100% 时告警（不抛错，避免把成功调用变失败）。"""
    if tokens <= 0 and cost <= 0 and calls <= 0:
        return
    for policy, _scope_key in matching_policies(
        db, yard_id, agent_code=agent_code, supplier_id=supplier_id, model=model, now=now
    ):
        amount = {
            "tokens": float(tokens),
            "cost": float(cost),
            "calls": float(calls),
        }.get(policy.metric, 0.0)
        if amount <= 0:
            continue
        used = _bump(
            db,
            yard_id=yard_id,
            scope_type=policy.scope_type,
            scope_key=policy.scope_key,
            metric=policy.metric,
            window=policy.window,
            amount=amount,
            now=now,
        )
        # 无限制策略仍记账（可观测），但不按上限告警（审查 B3）
        if is_unlimited(policy):
            continue
        limit = effective_limit(db, policy, now)
        if limit > 0:
            ratio = used / limit * 100
            for threshold in _ALERT_KEYS:
                if ratio >= threshold:
                    _alert(policy, used, limit, threshold)


# ---------------------------------------------------------------------------
# 申请与审批
# ---------------------------------------------------------------------------


def create_request(
    db: Session,
    *,
    yard_id: str,
    policy_id: str,
    requester_id: str,
    requester_name: str | None,
    requested_limit: float,
    reason: str,
    valid_days: int = 7,
) -> QuotaRequest:
    policy = db.get(QuotaPolicy, policy_id)
    if policy is None or policy.yard_id != yard_id:
        raise AppError("QUOTA_POLICY_NOT_FOUND", "额度策略不存在。", http_status=404)
    if requested_limit <= policy.limit_value:
        raise AppError(
            "INVALID_REQUEST",
            "申请额度必须高于当前上限。",
            http_status=400,
        )
    pending = _find_pending(db, policy.id, requester_id)
    if pending is not None:
        return pending
    row = QuotaRequest(
        yard_id=yard_id,
        policy_id=policy.id,
        scope_type=policy.scope_type,
        scope_key=policy.scope_key,
        metric=policy.metric,
        window=policy.window,
        current_limit=policy.limit_value,
        requested_limit=requested_limit,
        reason=reason,
        requester_id=requester_id,
        requester_name=requester_name,
        valid_days=max(0, int(valid_days)),
    )
    db.add(row)
    db.commit()
    return row


def approve_request(
    db: Session,
    request: QuotaRequest,
    *,
    decided_by: str,
    note: str = "",
    now: datetime | None = None,
) -> QuotaGrant:
    if request.status != STATUS_PENDING:
        raise AppError("QUOTA_REQUEST_CLOSED", "该申请已处理。", http_status=409)
    policy = db.get(QuotaPolicy, request.policy_id)
    if policy is None:
        raise AppError("QUOTA_POLICY_NOT_FOUND", "额度策略已删除，无法批额。", http_status=404)
    amount = float(request.requested_limit) - float(policy.limit_value)
    if amount <= 0:
        raise AppError(
            "INVALID_REQUEST",
            "申请额度不高于当前上限，无需批额。",
            http_status=400,
        )
    moment = now or datetime.now(UTC)
    days = int(request.valid_days or 0)
    expires = moment + timedelta(days=days) if days > 0 else _window_end(policy.window, moment)
    grant = QuotaGrant(
        yard_id=request.yard_id,
        policy_id=policy.id,
        amount=amount,
        expires_at=expires,
        request_id=request.id,
        created_by=decided_by,
    )
    request.status = STATUS_APPROVED
    request.decided_by = decided_by
    request.decided_at = moment
    request.decision_note = note
    db.add(grant)
    db.commit()
    return grant


def reject_request(
    db: Session,
    request: QuotaRequest,
    *,
    decided_by: str,
    note: str = "",
    now: datetime | None = None,
) -> None:
    if request.status != STATUS_PENDING:
        raise AppError("QUOTA_REQUEST_CLOSED", "该申请已处理。", http_status=409)
    request.status = "rejected"
    request.decided_by = decided_by
    request.decided_at = now or datetime.now(UTC)
    request.decision_note = note
    db.commit()
