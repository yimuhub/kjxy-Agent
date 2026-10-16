"""租户业务逻辑。只做业务规则；HTTP/鉴权在 router 层。"""

from sqlalchemy.orm import Session

from app.core.deps import TenantContext
from app.core.errors import AppError
from app.modules.audit.service import log_audit
from app.modules.auth.repository import get_by_username
from app.modules.tenants import repository as repo
from app.modules.tenants.models import ROLES
from app.modules.tenants.schemas import AddMemberRequest, TenantMemberOut, TenantOut

# platform_admin 是平台级角色，不作为租户成员角色授予。
TENANT_ASSIGNABLE_ROLES = [r for r in ROLES if r != "platform_admin"]


def _to_tenant_out(tenant) -> TenantOut:
    return TenantOut(
        tenant_id=tenant.id,
        name=tenant.name,
        status=tenant.status,
        province=tenant.province,
        city=tenant.city,
        scene_type=tenant.scene_type,
        config=tenant.config_json,
    )


def get_current_tenant(db: Session, ctx: TenantContext) -> TenantOut:
    tenant = repo.get_tenant(db, ctx.tenant_id)
    if tenant is None or tenant.status != "active":
        raise AppError("NOT_FOUND", "租户不存在或已停用。", http_status=404)
    return _to_tenant_out(tenant)


def list_members(
    db: Session, ctx: TenantContext, *, page: int, page_size: int
) -> tuple[list[TenantMemberOut], int]:
    rows, total = repo.list_members(db, ctx.tenant_id, page=page, page_size=page_size)
    items = [
        TenantMemberOut(
            user_id=user.id,
            username=user.username,
            display_name=user.display_name,
            email=user.email,
            role=membership.role,
            status=membership.status,
            joined_at=membership.created_at,
        )
        for membership, user in rows
    ]
    return items, total


def add_member(
    db: Session, ctx: TenantContext, req: AddMemberRequest, *, request_id: str
) -> TenantMemberOut:
    if req.role not in TENANT_ASSIGNABLE_ROLES:
        raise AppError(
            "INVALID_REQUEST",
            f"角色不合法，可选：{', '.join(TENANT_ASSIGNABLE_ROLES)}。",
            http_status=400,
        )
    user = get_by_username(db, req.username)
    if user is None:
        raise AppError("NOT_FOUND", "用户不存在。", http_status=404)
    if repo.get_membership(db, ctx.tenant_id, user.id) is not None:
        raise AppError("MEMBER_EXISTS", "该用户已是本租户成员。", http_status=409)

    if req.display_name and not user.display_name:
        user.display_name = req.display_name
    membership = repo.add_membership(db, tenant_id=ctx.tenant_id, user_id=user.id, role=req.role)
    log_audit(
        db,
        tenant_id=ctx.tenant_id,
        actor_id=ctx.user_id,
        action="member_added",
        resource_type="tenant_member",
        resource_id=user.id,
        result="success",
        request_id=request_id,
        metadata={"username": user.username, "role": req.role},
    )
    db.commit()
    return TenantMemberOut(
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        role=membership.role,
        status=membership.status,
        joined_at=membership.created_at,
    )


def remove_member(db: Session, ctx: TenantContext, user_id: str, *, request_id: str) -> None:
    membership = repo.get_membership(db, ctx.tenant_id, user_id)
    if membership is None:
        raise AppError("NOT_FOUND", "成员不存在。", http_status=404)
    if membership.role == "tenant_admin" and repo.count_active_admins(db, ctx.tenant_id) <= 1:
        raise AppError("CONFLICT", "不能移除最后一个租户管理员。", http_status=409)

    repo.remove_membership(db, membership)
    log_audit(
        db,
        tenant_id=ctx.tenant_id,
        actor_id=ctx.user_id,
        action="member_removed",
        resource_type="tenant_member",
        resource_id=user_id,
        result="success",
        request_id=request_id,
        metadata={"role": membership.role},
    )
    db.commit()
