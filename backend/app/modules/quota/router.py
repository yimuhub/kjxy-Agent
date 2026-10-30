"""分级额度接口（v3.32）。

- GET  /quota/overview / policies / requests / environments：读
- PUT  /quota/policies、DELETE /quota/policies/{id}、PUT /quota/environment：quota:manage
- POST /quota/requests：quota:request；approve/reject：quota:approve（不能批自己的单）
"""

from __future__ import annotations

from app.core.deps import CurrentUser, get_current_user, get_db_session, get_yard_context
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.audit.service import log_audit
from app.modules.quota import engine, environments, service
from app.modules.quota.models import QuotaCounter, QuotaPolicy
from app.modules.quota.schemas import (
    EnvironmentRequest,
    PolicyUpsertRequest,
    QuotaDecisionRequest,
    QuotaRequestBody,
)
from app.modules.yards.authz import has_permission
from fastapi import APIRouter, Depends, Request
from sqlalchemy import update
from sqlalchemy.orm import Session

router = APIRouter()

_READ_PERMISSION = "insights:read"
_MANAGE_PERMISSION = "quota:manage"
_REQUEST_PERMISSION = "quota:request"
_APPROVE_PERMISSION = "quota:approve"


def _allowed(db: Session, user: CurrentUser, permission: str) -> bool:
    if not user.yard_id:
        return False
    return has_permission(
        db,
        yard_id=user.yard_id,
        user_id=user.user_id,
        jwt_roles=user.roles,
        permission=permission,
    )


def _need(db: Session, user: CurrentUser, permission: str) -> None:
    if not _allowed(db, user, permission):
        raise AppError(
            "FORBIDDEN",
            f"缺少 {permission} 权限，无法执行该操作。",
            http_status=403,
        )


@router.get("/quota/overview")
def quota_overview(
    request: Request,
    ctx=Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    _need(db, user, _READ_PERMISSION)
    data = service.overview(db, ctx.yard_id)
    data["can_manage"] = _allowed(db, user, _MANAGE_PERMISSION)
    data["can_approve"] = _allowed(db, user, _APPROVE_PERMISSION)
    data["can_request"] = _allowed(db, user, _REQUEST_PERMISSION)
    return ok(data, request)


@router.get("/quota/environments")
def quota_environments(
    request: Request,
    ctx=Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    _need(db, user, _READ_PERMISSION)
    return ok({"environments": environments.payload()}, request)


@router.put("/quota/environment")
def quota_set_environment(
    body: EnvironmentRequest,
    request: Request,
    ctx=Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    _need(db, user, _MANAGE_PERMISSION)
    data = service.apply_environment(
        db, yard_id=ctx.yard_id, environment=body.environment, actor_id=user.user_id
    )
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=user.user_id,
        action="quota_environment_apply",
        resource_type="quota_policy",
        resource_id=body.environment,
        result="success",
        request_id=user.request_id,
        metadata={"environment": body.environment},
    )
    db.commit()
    return ok(data, request)


@router.put("/quota/policies")
def quota_upsert_policy(
    body: PolicyUpsertRequest,
    request: Request,
    ctx=Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    _need(db, user, _MANAGE_PERMISSION)
    data = service.upsert_policy(
        db, yard_id=ctx.yard_id, actor_id=user.user_id, payload=body.model_dump()
    )
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=user.user_id,
        action="quota_policy_upsert",
        resource_type="quota_policy",
        resource_id=data["id"],
        result="success",
        request_id=user.request_id,
        metadata={
            "scope": [body.scope_type, body.scope_key],
            "metric": body.metric,
            "window": body.window,
            "limit": body.limit_value,
            "on_exceed": body.on_exceed,
        },
    )
    db.commit()
    return ok(data, request)


@router.delete("/quota/policies/{policy_id}")
def quota_delete_policy(
    policy_id: str,
    request: Request,
    ctx=Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    _need(db, user, _MANAGE_PERMISSION)
    service.delete_policy(db, yard_id=ctx.yard_id, policy_id=policy_id)
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=user.user_id,
        action="quota_policy_delete",
        resource_type="quota_policy",
        resource_id=policy_id,
        result="success",
        request_id=user.request_id,
    )
    db.commit()
    return ok({"deleted": policy_id}, request)


@router.post("/quota/policies/{policy_id}/reset")
def quota_reset_counter(
    policy_id: str,
    request: Request,
    ctx=Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """清零某条策略当前窗口的用量（跨窗口的历史不动）。"""
    _need(db, user, _MANAGE_PERMISSION)
    policy = db.get(QuotaPolicy, policy_id)
    if policy is None or policy.yard_id != ctx.yard_id:
        raise AppError("QUOTA_POLICY_NOT_FOUND", "额度策略不存在。", http_status=404)
    start = engine.window_start(policy.window)
    db.execute(
        update(QuotaCounter)
        .where(
            QuotaCounter.yard_id == ctx.yard_id,
            QuotaCounter.scope_type == policy.scope_type,
            QuotaCounter.scope_key == policy.scope_key,
            QuotaCounter.metric == policy.metric,
            QuotaCounter.window == policy.window,
            QuotaCounter.window_start == start,
        )
        .values(used=0)
        .execution_options(synchronize_session=False)
    )
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=user.user_id,
        action="quota_policy_reset",
        resource_type="quota_policy",
        resource_id=policy_id,
        result="success",
        request_id=user.request_id,
        metadata={"window_start": start},
    )
    db.commit()
    return ok({"policy_id": policy_id, "window_start": start, "used": 0}, request)


@router.get("/quota/requests")
def quota_list_requests(
    request: Request,
    status: str | None = None,
    ctx=Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    # 能看全量：有 insights:read 或审批权；只能看自己的：有 quota:request
    can_read_all = _allowed(db, user, _READ_PERMISSION) or _allowed(db, user, _APPROVE_PERMISSION)
    if not can_read_all:
        _need(db, user, _REQUEST_PERMISSION)
    can_approve = _allowed(db, user, _APPROVE_PERMISSION)
    rows = service.list_requests(
        db,
        yard_id=ctx.yard_id,
        status=status,
        requester_id=None if can_read_all else user.user_id,
    )
    return ok({"items": rows, "can_approve": can_approve}, request)


@router.post("/quota/requests")
def quota_create_request(
    body: QuotaRequestBody,
    request: Request,
    ctx=Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    _need(db, user, _REQUEST_PERMISSION)
    row = engine.create_request(
        db,
        yard_id=ctx.yard_id,
        policy_id=body.policy_id,
        requester_id=user.user_id,
        requester_name=None,
        requested_limit=body.requested_limit,
        reason=body.reason,
        valid_days=body.valid_days,
    )
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=user.user_id,
        action="quota_request_create",
        resource_type="quota_request",
        resource_id=row.id,
        result="success",
        request_id=user.request_id,
        metadata={"requested_limit": body.requested_limit},
    )
    db.commit()
    return ok(service._request_payload(row), request)


@router.post("/quota/requests/{request_id}/approve")
def quota_approve(
    request_id: str,
    body: QuotaDecisionRequest,
    request: Request,
    ctx=Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    _need(db, user, _APPROVE_PERMISSION)
    row = service.get_request(db, yard_id=ctx.yard_id, request_id=request_id)
    if row.requester_id == user.user_id and "platform_admin" not in user.roles:
        raise AppError(
            "FORBIDDEN",
            "不能审批自己提交的提额申请，请由其他管理员处理。",
            http_status=403,
        )
    if body.approved_limit is not None:
        row.requested_limit = float(body.approved_limit)
    grant = engine.approve_request(db, row, decided_by=user.user_id, note=body.note)
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=user.user_id,
        action="quota_request_approve",
        resource_type="quota_request",
        resource_id=row.id,
        result="success",
        request_id=user.request_id,
        metadata={
            "amount": grant.amount,
            "expires_at": grant.expires_at.isoformat(),
            "policy_id": row.policy_id,
        },
    )
    db.commit()
    return ok(
        {
            "request": service._request_payload(row),
            "grant": {
                "id": grant.id,
                "amount": grant.amount,
                "expires_at": grant.expires_at.isoformat(),
            },
        },
        request,
    )


@router.post("/quota/requests/{request_id}/reject")
def quota_reject(
    request_id: str,
    body: QuotaDecisionRequest,
    request: Request,
    ctx=Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    _need(db, user, _APPROVE_PERMISSION)
    row = service.get_request(db, yard_id=ctx.yard_id, request_id=request_id)
    if row.requester_id == user.user_id and "platform_admin" not in user.roles:
        raise AppError("FORBIDDEN", "不能审批自己提交的申请。", http_status=403)
    engine.reject_request(db, row, decided_by=user.user_id, note=body.note)
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=user.user_id,
        action="quota_request_reject",
        resource_type="quota_request",
        resource_id=row.id,
        result="success",
        request_id=user.request_id,
        metadata={"note": body.note},
    )
    db.commit()
    return ok(service._request_payload(row), request)
