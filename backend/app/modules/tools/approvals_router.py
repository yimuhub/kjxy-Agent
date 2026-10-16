"""高风险工具审批单接口。

- GET /tool-approvals：本租户审批单列表（租户成员可见）。
- POST /tool-approvals/{id}/approve：批准（租户管理员/专家），批准后立即执行工具；
  若关联任务处于 awaiting_review，任务置为 succeeded 并写入工具执行结果。
- POST /tool-approvals/{id}/reject：驳回（租户管理员/专家）；
  若关联任务处于 awaiting_review，任务置为 cancelled。
"""

import asyncio

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import (
    CurrentUser,
    TenantContext,
    get_current_user,
    get_db_session,
    get_tenant_context,
)
from app.core.errors import AppError
from app.core.responses import ok, paged
from app.modules.audit.service import log_audit
from app.modules.auth.models import User
from app.modules.tasks import state_machine as sm
from app.modules.tasks.models import Task
from app.modules.tasks.service import TaskService
from app.modules.tenants import repository as tenant_repo
from app.modules.tools import approvals as approval_svc
from app.modules.tools.models import ToolApproval

router = APIRouter(prefix="/tool-approvals", tags=["tool-approvals"])

#: 可审批角色：租户管理员 / 专家（平台管理员走 get_tenant_context 同样被拒，保持租户边界）
_APPROVER_ROLES = frozenset({"tenant_admin", "expert"})


class ApprovalDecisionRequest(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


def _approval_out(a: ToolApproval) -> dict:
    return {
        "id": a.id,
        "tenant_id": a.tenant_id,
        "tool_code": a.tool_code,
        "params": a.params_json or {},
        "status": a.status,
        "requested_by": a.requested_by,
        "task_id": a.task_id,
        "reason": a.reason,
        "decided_by": a.decided_by,
        "decided_at": a.decided_at.isoformat() if a.decided_at else None,
        "decision_note": a.decision_note,
        "expires_at": a.expires_at.isoformat() if a.expires_at else None,
        "used_count": a.used_count,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def _require_approver(user: CurrentUser) -> TenantContext:
    """审批人上下文：必须有租户 + 审批角色。"""
    if not user.tenant_id:
        raise AppError("TENANT_REQUIRED", "该操作需要租户上下文。", http_status=400)
    if "platform_admin" not in user.roles and not (set(user.roles) & _APPROVER_ROLES):
        raise AppError("FORBIDDEN", "需要租户管理员或专家角色才能审批。", http_status=403)
    return TenantContext(
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        roles=user.roles,
        request_id=user.request_id,
    )


def _requester_roles(db: Session, tenant_id: str, user_id: str) -> list[str]:
    """解析原始请求人的角色（审批执行工具时做权限校验用）。"""
    roles: list[str] = []
    membership = tenant_repo.get_membership(db, tenant_id, user_id)
    if membership is not None:
        roles = [membership.role]
    user = db.get(User, user_id)
    if (
        user is not None
        and user.username in settings.platform_admin_username_list
        and "platform_admin" not in roles
    ):
        roles = ["platform_admin", *roles]
    return roles


def _ctx_from_tenant(db: Session, tenant_id: str, user_id: str, request_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        user_id=user_id,
        roles=_requester_roles(db, tenant_id, user_id),
        request_id=request_id,
    )


@router.get("")
def list_approvals(
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """本租户审批单列表（租户成员可见；审批动作需审批角色）。"""
    rows, total = approval_svc.list_approvals(
        db, ctx.tenant_id, status=status, page=page, page_size=page_size
    )
    return paged([_approval_out(r) for r in rows], page, page_size, total, request)


@router.post("/{approval_id}/approve")
def approve(
    approval_id: str,
    body: ApprovalDecisionRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    ctx = _require_approver(user)
    approval = approval_svc.get_approval(db, ctx.tenant_id, approval_id)
    approval_svc.approve_approval(
        db, approval, decided_by=ctx.user_id, note=body.note, request_id=ctx.request_id
    )

    # 批准后立即以请求人身份执行工具（审批单已 approved，注册表 DB 校验放行）
    from app.modules.tools.registry import tool_registry

    req_ctx = _ctx_from_tenant(db, approval.tenant_id, approval.requested_by, ctx.request_id)
    try:
        tool_result = asyncio.run(
            tool_registry.execute(
                db, approval.tool_code, dict(approval.params_json or {}), req_ctx, ctx.request_id
            )
        )
    except AppError as e:
        db.rollback()
        raise AppError(
            "APPROVAL_EXEC_FAILED",
            f"审批通过但工具执行失败：{e.message}",
            http_status=502,
            details={"approval_id": approval.id, "tool_error": e.code},
        ) from e

    # 关联任务：awaiting_review → succeeded，写入工具执行结果
    task_id = approval.task_id
    if task_id:
        task = db.get(Task, task_id)
        if (
            task is not None
            and task.tenant_id == ctx.tenant_id
            and task.status == sm.STATUS_AWAITING_REVIEW
        ):
            TaskService.set_status(
                db,
                task,
                sm.STATUS_SUCCEEDED,
                event_message="审批通过，工具已执行。",
                result={
                    "approval_id": approval.id,
                    "tool_code": approval.tool_code,
                    "tool_result": tool_result,
                },
            )
    db.commit()
    log_audit(
        db,
        tenant_id=ctx.tenant_id,
        actor_id=ctx.user_id,
        action="tool_approval_executed",
        resource_type="tool_approval",
        resource_id=approval.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"tool_code": approval.tool_code, "task_id": task_id},
    )
    db.commit()
    return ok({"approval": _approval_out(approval), "tool_result": tool_result}, request)


@router.post("/{approval_id}/reject")
def reject(
    approval_id: str,
    body: ApprovalDecisionRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    ctx = _require_approver(user)
    approval = approval_svc.get_approval(db, ctx.tenant_id, approval_id)
    approval_svc.reject_approval(
        db, approval, decided_by=ctx.user_id, note=body.note, request_id=ctx.request_id
    )

    task_id = approval.task_id
    if task_id:
        task = db.get(Task, task_id)
        if (
            task is not None
            and task.tenant_id == ctx.tenant_id
            and task.status == sm.STATUS_AWAITING_REVIEW
        ):
            TaskService.set_status(
                db,
                task,
                sm.STATUS_CANCELLED,
                event_message="审批被驳回，任务取消。",
                error_message=f"审批驳回：{body.note or '未填写理由'}",
            )
    db.commit()
    return ok({"approval": _approval_out(approval)}, request)
