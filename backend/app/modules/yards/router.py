"""小院路由：只做鉴权/校验与 service 调用，业务逻辑在 service 层。"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.deps import (
    YardContext,
    get_db_session,
    get_yard_context,
)
from app.core.errors import AppError
from app.core.pagination import pagination_params
from app.core.responses import ok, paged
from app.modules.yards import authz, member_service, registration_service, service
from app.modules.yards import invitations as invitation_service
from app.modules.yards.permissions import has_permission
from app.modules.yards.schemas import (
    BatchAddMembersRequest,
    BatchAddMembersResult,
    BatchMemberFailure,
    InvitationCreateRequest,
    MemberCreateRequest,
    MemberCreateResult,
    MemberPasswordResetResult,
    MemberUpdateRequest,
    RegistrationApproveResult,
    RegistrationRejectRequest,
    YardMemberOut,
    YardOut,
)

router = APIRouter()


def require_permission(permission: str):
    """基于权限点的守卫；platform_admin 因拥有全部权限自然通过。"""

    def checker(ctx: YardContext = Depends(get_yard_context)) -> YardContext:
        if not has_permission(ctx.roles, permission):
            raise AppError("FORBIDDEN", "权限不足。", http_status=403)
        return ctx

    return checker


def require_yard_any_permission(*permissions: str):
    """小院级权限守卫：满足任一权限点即可（DB 角色覆盖 + 个人授权）。"""

    def checker(
        ctx: YardContext = Depends(get_yard_context),
        db: Session = Depends(get_db_session),
    ) -> YardContext:
        for permission in permissions:
            if authz.has_permission(
                db,
                yard_id=ctx.yard_id,
                user_id=ctx.user_id,
                jwt_roles=ctx.roles,
                permission=permission,
            ):
                return ctx
        raise AppError("FORBIDDEN", "权限不足。", http_status=403)

    return checker


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "req_unknown")


@router.get("/yards/current")
def current_yard(
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db=Depends(get_db_session),
):
    data: YardOut = service.get_current_yard(db, ctx)
    return ok(data.model_dump(), request)


@router.get("/yards/current/members")
def list_members(
    request: Request,
    # 成员目录对同院成员可见：无 members:manage 时联系方式脱敏（兼容既有前端/安全测试）
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
    page_params=Depends(pagination_params),
):
    page, page_size = page_params
    items, total = member_service.list_members(db, ctx, page=page, page_size=page_size)
    return paged([i.model_dump() for i in items], page, page_size, total, request)


@router.post("/yards/current/members", status_code=201)
def add_member(
    req: MemberCreateRequest,
    request: Request,
    ctx: YardContext = Depends(require_yard_any_permission("members:manage")),
    db: Session = Depends(get_db_session),
):
    data: MemberCreateResult = member_service.create_member(
        db, ctx, req, request_id=_request_id(request)
    )
    return ok(data.model_dump(mode="json"), request)


@router.patch("/yards/current/members/{user_id}")
def update_member(
    user_id: str,
    req: MemberUpdateRequest,
    request: Request,
    ctx: YardContext = Depends(
        require_yard_any_permission("members:manage", "members:manage_limited")
    ),
    db: Session = Depends(get_db_session),
):
    data: YardMemberOut = member_service.update_member(
        db, ctx, user_id, req, request_id=_request_id(request)
    )
    return ok(data.model_dump(mode="json"), request)


@router.post("/yards/current/members/{user_id}/reset-password")
def reset_member_password(
    user_id: str,
    request: Request,
    ctx: YardContext = Depends(require_yard_any_permission("members:reset_password")),
    db: Session = Depends(get_db_session),
):
    temporary, member = member_service.reset_member_password(
        db, ctx, user_id, request_id=_request_id(request)
    )
    data = MemberPasswordResetResult(user_id=member.user_id, temporary_password=temporary)
    return ok({**data.model_dump(), "member": member.model_dump(mode="json")}, request)


@router.get("/yards/current/registrations")
def list_registrations(
    request: Request,
    status: str | None = Query(default=None, description="pending|approved|rejected"),
    ctx: YardContext = Depends(require_yard_any_permission("members:approve")),
    db: Session = Depends(get_db_session),
):
    items = registration_service.list_registrations(db, ctx.yard_id, status=status)
    return ok([i.model_dump(mode="json") for i in items], request)


@router.post("/yards/current/registrations/{registration_id}/approve")
def approve_registration(
    registration_id: str,
    request: Request,
    ctx: YardContext = Depends(require_yard_any_permission("members:approve")),
    db: Session = Depends(get_db_session),
):
    join_request, member = registration_service.approve_registration(
        db, ctx, registration_id, request_id=_request_id(request)
    )
    data = RegistrationApproveResult(request=join_request, member=member)
    return ok(data.model_dump(mode="json"), request)


@router.post("/yards/current/registrations/{registration_id}/reject")
def reject_registration(
    registration_id: str,
    request: Request,
    req: RegistrationRejectRequest | None = None,
    ctx: YardContext = Depends(require_yard_any_permission("members:approve")),
    db: Session = Depends(get_db_session),
):
    data = registration_service.reject_registration(
        db,
        ctx,
        registration_id,
        reason=req.reason if req else None,
        request_id=_request_id(request),
    )
    return ok(data.model_dump(mode="json"), request)


@router.post("/yards/current/members/batch", status_code=201)
def batch_add_members(
    req: BatchAddMembersRequest,
    request: Request,
    ctx: YardContext = Depends(require_permission("members:manage")),
    db=Depends(get_db_session),
):
    """批量添加已有账号：逐条独立成败，返回成功列表与失败原因。"""
    added, failed = service.batch_add_members(
        db,
        ctx,
        [(m.username, m.role, m.display_name) for m in req.members],
        request_id=_request_id(request),
    )
    data = BatchAddMembersResult(
        added=added,
        failed=[BatchMemberFailure(**item) for item in failed],
    )
    return ok(data.model_dump(), request)


@router.get("/yards/current/invitations")
def list_invitations(
    request: Request,
    ctx: YardContext = Depends(require_permission("members:manage")),
    db=Depends(get_db_session),
):
    items = invitation_service.list_invitations(db, ctx)
    return ok([i.model_dump(mode="json") for i in items], request)


@router.post("/yards/current/invitations", status_code=201)
def create_invitation(
    req: InvitationCreateRequest,
    request: Request,
    ctx: YardContext = Depends(require_permission("members:manage")),
    db=Depends(get_db_session),
):
    data, _token = invitation_service.create_invitation(
        db,
        ctx,
        role=req.role,
        expires_in_days=req.expires_in_days,
        note=req.note,
        request_id=_request_id(request),
    )
    return JSONResponse(status_code=201, content=ok(data.model_dump(mode="json"), request))


@router.delete("/yards/current/invitations/{invitation_id}")
def revoke_invitation(
    invitation_id: str,
    request: Request,
    ctx: YardContext = Depends(require_permission("members:manage")),
    db=Depends(get_db_session),
):
    data = invitation_service.revoke_invitation(
        db, ctx, invitation_id, request_id=_request_id(request)
    )
    return ok(data.model_dump(mode="json"), request)


@router.delete("/yards/current/members/{user_id}")
def remove_member(
    user_id: str,
    request: Request,
    ctx: YardContext = Depends(require_yard_any_permission("members:manage")),
    db: Session = Depends(get_db_session),
):
    member_service.remove_member(db, ctx, user_id, request_id=_request_id(request))
    return ok({"ok": True}, request)
