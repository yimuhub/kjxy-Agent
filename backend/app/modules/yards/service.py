"""小院业务逻辑。只做业务规则；HTTP/鉴权在 router 层。"""

from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.audit.service import log_audit
from app.modules.auth.repository import get_by_username
from app.modules.yards import repository as repo
from app.modules.yards.permissions import ASSIGNABLE_ROLES
from app.modules.yards.schemas import AddMemberRequest, YardMemberOut, YardOut

# 可分配的常规角色（负责人只能通过“转让”产生，平台管理员不参与小院）
YARD_ASSIGNABLE_ROLES = list(ASSIGNABLE_ROLES)


def _to_yard_out(yard) -> YardOut:
    return YardOut(
        yard_id=yard.id,
        name=yard.name,
        status=yard.status,
        province=yard.province,
        city=yard.city,
        scene_type=yard.scene_type,
        config=yard.config_json,
    )


def get_current_yard(db: Session, ctx: YardContext) -> YardOut:
    yard = repo.get_yard(db, ctx.yard_id)
    if yard is None or yard.status != "active":
        raise AppError("NOT_FOUND", "小院不存在或已停用。", http_status=404)
    return _to_yard_out(yard)


def list_members(
    db: Session, ctx: YardContext, *, page: int, page_size: int
) -> tuple[list[YardMemberOut], int]:
    rows, total = repo.list_members(db, ctx.yard_id, page=page, page_size=page_size)
    items = [
        YardMemberOut(
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
    db: Session, ctx: YardContext, req: AddMemberRequest, *, request_id: str
) -> YardMemberOut:
    data = _add_member(
        db,
        ctx,
        username=req.username,
        role=req.role,
        display_name=req.display_name,
        request_id=request_id,
    )
    db.commit()
    return data


def _add_member(
    db: Session,
    ctx: YardContext,
    *,
    username: str,
    role: str,
    display_name: str | None,
    request_id: str,
) -> YardMemberOut:
    """添加成员但不提交事务；批量导入按条复用并统一提交。"""
    if role not in YARD_ASSIGNABLE_ROLES:
        raise AppError(
            "INVALID_REQUEST",
            f"角色不合法，可选：{', '.join(YARD_ASSIGNABLE_ROLES)}。",
            http_status=400,
        )
    user = get_by_username(db, username)
    if user is None:
        raise AppError("NOT_FOUND", "用户不存在。", http_status=404)
    if repo.get_membership(db, ctx.yard_id, user.id) is not None:
        raise AppError("MEMBER_EXISTS", "该用户已是本小院成员。", http_status=409)

    if display_name and not user.display_name:
        user.display_name = display_name
    membership = repo.add_membership(db, yard_id=ctx.yard_id, user_id=user.id, role=role)
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="member_added",
        resource_type="yard_member",
        resource_id=user.id,
        result="success",
        request_id=request_id,
        metadata={"username": user.username, "role": role},
    )
    db.flush()
    return YardMemberOut(
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        role=membership.role,
        status=membership.status,
        joined_at=membership.created_at,
    )


def batch_add_members(
    db: Session,
    ctx: YardContext,
    items: list[tuple[str, str, str | None]],
    *,
    request_id: str,
) -> tuple[list[YardMemberOut], list[dict[str, str]]]:
    """批量添加成员：逐条独立提交（savepoint），单条失败不影响其余。"""
    added: list[YardMemberOut] = []
    failed: list[dict[str, str]] = []
    for username, role, display_name in items:
        try:
            with db.begin_nested():
                added.append(
                    _add_member(
                        db,
                        ctx,
                        username=username,
                        role=role,
                        display_name=display_name,
                        request_id=request_id,
                    )
                )
        except AppError as exc:
            failed.append({"username": username, "error_code": exc.code, "message": exc.message})
    db.commit()
    return added, failed


def remove_member(db: Session, ctx: YardContext, user_id: str, *, request_id: str) -> None:
    membership = repo.get_membership(db, ctx.yard_id, user_id)
    if membership is None:
        raise AppError("NOT_FOUND", "成员不存在。", http_status=404)
    if membership.role == "yard_admin" and repo.count_active_admins(db, ctx.yard_id) <= 1:
        raise AppError("CONFLICT", "不能移除最后一个小院管理员。", http_status=409)

    repo.remove_membership(db, membership)
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="member_removed",
        resource_type="yard_member",
        resource_id=user_id,
        result="success",
        request_id=request_id,
        metadata={"role": membership.role},
    )
    db.commit()
