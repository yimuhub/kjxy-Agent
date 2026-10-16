"""高风险工具审批单 service。

流程：
1. 工具注册表拦截 external_side_effect 调用时，调用 get_or_create_pending_approval
   建单（同租户+同工具+同参数哈希去重），调用方拿到 approval_id 去走人工审批。
2. 审批人调 approve/reject；通过后调用方凭已批准单重试，_is_approved 放行。
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.audit.service import log_audit
from app.modules.tools.models import (
    APPROVAL_APPROVED,
    APPROVAL_CANCELLED,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    ToolApproval,
)

log = get_logger(__name__)

#: 计算 params_hash 时剔除的易变键（审批标记本身不参与去重）
_VOLATILE_PARAM_KEYS = frozenset({"approved", "_approval_id"})


def _utcnow() -> datetime:
    return datetime.now(UTC)


def params_hash(params: dict | None) -> str:
    """参数规范哈希：排序键 JSON → sha256，用于同义调用去重。"""
    clean = {k: v for k, v in (params or {}).items() if k not in _VOLATILE_PARAM_KEYS}
    canonical = json.dumps(clean, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_or_create_pending_approval(
    db: Session,
    *,
    tenant_id: str,
    tool_code: str,
    params: dict | None,
    requested_by: str,
    task_id: str | None = None,
    reason: str | None = None,
    request_id: str = "req_unknown",
) -> ToolApproval:
    """获取或创建 pending 审批单；幂等（同租户+工具+参数哈希复用未关闭单）。"""
    phash = params_hash(params)
    existing = (
        db.query(ToolApproval)
        .filter(
            ToolApproval.tenant_id == tenant_id,
            ToolApproval.tool_code == tool_code,
            ToolApproval.params_hash == phash,
            ToolApproval.status == APPROVAL_PENDING,
        )
        .order_by(ToolApproval.created_at.desc())
        .first()
    )
    if existing is not None:
        # 补关联任务（首次为直接调用、后续走任务时）
        if task_id and not existing.task_id:
            existing.task_id = task_id
            db.flush()
        return existing

    approval = ToolApproval(
        tenant_id=tenant_id,
        tool_code=tool_code,
        params_json=dict(params or {}),
        params_hash=phash,
        status=APPROVAL_PENDING,
        requested_by=requested_by,
        task_id=task_id,
        reason=reason,
    )
    db.add(approval)
    db.flush()
    log_audit(
        db,
        tenant_id=tenant_id,
        actor_id=requested_by,
        action="tool_approval_requested",
        resource_type="tool_approval",
        resource_id=approval.id,
        result="success",
        request_id=request_id,
        metadata={"tool_code": tool_code, "task_id": task_id},
    )
    log.info("tool approval created: %s %s", tool_code, approval.id)
    return approval


def get_approval(db: Session, tenant_id: str, approval_id: str) -> ToolApproval:
    approval = (
        db.query(ToolApproval)
        .filter(ToolApproval.id == approval_id, ToolApproval.tenant_id == tenant_id)
        .first()
    )
    if approval is None:
        raise AppError("APPROVAL_NOT_FOUND", "审批单不存在。", http_status=404)
    return approval


def list_approvals(
    db: Session,
    tenant_id: str,
    *,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[ToolApproval], int]:
    q = db.query(ToolApproval).filter(ToolApproval.tenant_id == tenant_id)
    if status:
        q = q.filter(ToolApproval.status == status)
    total = q.count()
    rows = (
        q.order_by(ToolApproval.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return rows, total


def has_approved_approval(
    db: Session, *, tenant_id: str, tool_code: str, params: dict | None
) -> bool:
    """是否存在针对同一调用的、仍有效的已批准单（批准后重试放行用）。

    有效性 = status=approved 且未过期（expires_at 为空视为永久）。
    命中即放行并累加 used_count；超过 tool_approval_max_uses 上限后不再放行。
    """
    ttl_hours = settings.tool_approval_ttl_hours
    max_uses = settings.tool_approval_max_uses
    now = _utcnow()

    filters = [
        ToolApproval.tenant_id == tenant_id,
        ToolApproval.tool_code == tool_code,
        ToolApproval.params_hash == params_hash(params),
        ToolApproval.status == APPROVAL_APPROVED,
    ]
    if ttl_hours and ttl_hours > 0:
        # 未设 expires_at 的历史行视为仍在有效期内（向后兼容）
        filters.append(or_(ToolApproval.expires_at.is_(None), ToolApproval.expires_at > now))

    approval = (
        db.query(ToolApproval)
        .filter(*filters)
        .order_by(ToolApproval.decided_at.desc().nullslast())
        .first()
    )
    if approval is None:
        return False
    if max_uses and max_uses > 0 and approval.used_count >= max_uses:
        return False
    approval.used_count += 1
    db.flush()
    return True


def _ensure_decidable(approval: ToolApproval, decided_by: str) -> None:
    if approval.status != APPROVAL_PENDING:
        raise AppError(
            "APPROVAL_NOT_PENDING",
            f"审批单已处理（{approval.status}），不可重复决策。",
            http_status=409,
        )
    if approval.requested_by == decided_by:
        raise AppError("APPROVAL_SELF_DECIDE", "不能审批自己发起的申请。", http_status=403)


def approve_approval(
    db: Session,
    approval: ToolApproval,
    *,
    decided_by: str,
    note: str | None = None,
    request_id: str = "req_unknown",
) -> ToolApproval:
    _ensure_decidable(approval, decided_by)
    approval.status = APPROVAL_APPROVED
    approval.decided_by = decided_by
    decided_at = _utcnow()
    approval.decided_at = decided_at
    approval.decision_note = note
    # 有效期与复用上限（规范 §15.2 R3：审批不是永久通行证）
    approval.expires_at = (
        decided_at + timedelta(hours=settings.tool_approval_ttl_hours)
        if settings.tool_approval_ttl_hours > 0
        else None
    )
    approval.used_count = 0
    db.flush()
    log_audit(
        db,
        tenant_id=approval.tenant_id,
        actor_id=decided_by,
        action="tool_approval_approved",
        resource_type="tool_approval",
        resource_id=approval.id,
        result="success",
        request_id=request_id,
        metadata={"tool_code": approval.tool_code, "task_id": approval.task_id},
    )
    return approval


def reject_approval(
    db: Session,
    approval: ToolApproval,
    *,
    decided_by: str,
    note: str | None = None,
    request_id: str = "req_unknown",
) -> ToolApproval:
    _ensure_decidable(approval, decided_by)
    approval.status = APPROVAL_REJECTED
    approval.decided_by = decided_by
    approval.decided_at = _utcnow()
    approval.decision_note = note
    db.flush()
    log_audit(
        db,
        tenant_id=approval.tenant_id,
        actor_id=decided_by,
        action="tool_approval_rejected",
        resource_type="tool_approval",
        resource_id=approval.id,
        result="success",
        request_id=request_id,
        metadata={"tool_code": approval.tool_code, "task_id": approval.task_id},
    )
    return approval


def cancel_approval(
    db: Session,
    approval: ToolApproval,
    *,
    actor_id: str,
    request_id: str = "req_unknown",
) -> ToolApproval:
    """申请人撤销未决策的审批单。"""
    if approval.status != APPROVAL_PENDING:
        raise AppError("APPROVAL_NOT_PENDING", "审批单已处理，不可撤销。", http_status=409)
    if approval.requested_by != actor_id:
        raise AppError("APPROVAL_FORBIDDEN", "只能撤销自己发起的审批单。", http_status=403)
    approval.status = APPROVAL_CANCELLED
    db.flush()
    log_audit(
        db,
        tenant_id=approval.tenant_id,
        actor_id=actor_id,
        action="tool_approval_cancelled",
        resource_type="tool_approval",
        resource_id=approval.id,
        result="success",
        request_id=request_id,
        metadata={"tool_code": approval.tool_code},
    )
    return approval
