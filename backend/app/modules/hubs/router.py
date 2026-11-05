"""枢纽 REST API（/hubs，v3.65 / v3.66）：通用基座上的运营模式选择。

v3.66 起枢纽端点不再依赖「必须有小院」的 ``get_yard_context``——纯枢纽账号
（只挂 hub_members、不属任何小院）也应能登录并管理自己那批院。身份仍来自 JWT，
判权仍走 ``require_hub_access``（platform_admin / hub manager / viewer / 所辖院管理员）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.deps import (
    CurrentUser,
    YardContext,
    get_current_user,
    get_db_session,
)
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.hubs import service

router = APIRouter(prefix="/hubs", tags=["hubs"])


class HubCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=120)
    mode: str = Field(pattern="^(province|municipal|station)$")
    region: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=500)


class HubAttachRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    yard_id: str = Field(min_length=6, max_length=36)
    #: v3.67 关系口径：日常管理 / 评审归口 / 观察（同一院可同时有多条）
    relation_type: str = Field(default="manage", pattern="^(manage|review|observe)$")


class HubMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=2, max_length=36)
    role: str = Field(pattern="^(manager|viewer)$")


def _hub_ctx(user: CurrentUser) -> YardContext:
    """枢纽端点的身份上下文。

    纯枢纽账号没有小院，``yard_id`` 用空串承载——service 只用 user_id/roles/request_id
    做判权，不会拿它去查小院数据。
    """
    return YardContext(
        yard_id=user.yard_id or "",
        user_id=user.user_id,
        roles=list(user.roles or []),
        request_id=user.request_id,
    )


@router.get("")
def list_hubs(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """我可见的枢纽：platform_admin 全部；普通用户按 hub_members / 所辖院。"""
    visible = service.list_visible_hubs(
        db, user.user_id, is_platform_admin="platform_admin" in (user.roles or [])
    )
    items = []
    for hub, my_role in visible:
        out_row = service.hub_out(db, hub)
        out_row["my_role"] = my_role
        items.append(out_row)
    return ok({"items": items}, request)


@router.post("")
def create_hub(
    payload: HubCreateRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """创建枢纽并选择运营模式（创建者自动成为 manager）。

    v3.66 收口：只有平台管理员或已有枢纽的 manager 能建。
    小院管理员默认带 ``hubs:manage``（历史遗留），不再自动获得建枢纽能力，
    避免任何小院管理员都能凭空造组织。
    """
    is_platform_admin = "platform_admin" in (user.roles or [])
    allowed = is_platform_admin or "hubs:manage" in service.hub_permissions(
        db, user.user_id, is_platform_admin=is_platform_admin
    )
    if not allowed:
        raise AppError("FORBIDDEN", "只有平台管理员或枢纽管理员可创建枢纽。", http_status=403)
    hub = service.create_hub(
        db,
        _hub_ctx(user),
        name=payload.name,
        mode=payload.mode,
        region=payload.region,
        note=payload.note,
    )
    db.commit()
    return ok(service.hub_out(db, hub), request)


@router.get("/attachable-yards")
def attachable_yards(
    request: Request,
    hub_id: str | None = Query(default=None, max_length=36),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """当前用户可关联到枢纽的小院清单（platform_admin 全部；普通用户为其管理的小院）。

    带 ``hub_id`` 时额外标记 ``in_current_hub``，前端据此禁用「已在本枢纽」的项。
    """
    return ok(service.list_attachable_yards(db, user, hub_id), request)


@router.get("/{hub_id}/overview")
def hub_overview(
    hub_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """枢纽工作台：所辖小院聚合（只出数字，不含日志内容）+ 模式化分组洞察。"""
    return ok(service.hub_overview(db, _hub_ctx(user), hub_id), request)


@router.post("/{hub_id}/yards")
def attach_yard(
    hub_id: str,
    payload: HubAttachRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """把某院关联到本枢纽（可指定 manage/review/observe，已属他院不再冲突）。"""
    result = service.attach_yard(db, _hub_ctx(user), hub_id, payload.yard_id, payload.relation_type)
    db.commit()
    return ok(result, request)


@router.delete("/{hub_id}/yards/{yard_id}")
def detach_yard(
    hub_id: str,
    yard_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """解除本枢纽与该院的关系（只删这一条，其他枢纽的关联不受影响）。"""
    result = service.detach_yard(db, _hub_ctx(user), hub_id, yard_id)
    db.commit()
    return ok(result, request)


@router.delete("/yards/{yard_id}")
def detach_yard_legacy(
    yard_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """兼容旧路径：按该院主归属推断枢纽（v3.67 前的前端与脚本仍可用）。"""
    from app.modules.yards.models import Yard

    yard = db.get(Yard, yard_id)
    if yard is None or not yard.hub_id:
        raise AppError("YARD_NOT_ATTACHED", "该小院未归属任何枢纽。", http_status=404)
    result = service.detach_yard(db, _hub_ctx(user), yard.hub_id, yard_id)
    db.commit()
    return ok(result, request)


@router.get("/{hub_id}/member-candidates")
def member_candidates(
    hub_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """可添加为枢纽成员的活跃用户清单（已在本枢纽的排除）。"""
    service.require_hub_access(db, _hub_ctx(user), hub_id, manager=True)
    return ok(service.member_candidates(db, _hub_ctx(user), hub_id), request)


@router.post("/{hub_id}/members")
def add_member(
    hub_id: str,
    payload: HubMemberRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    result = service.add_member(db, _hub_ctx(user), hub_id, payload.user_id, payload.role)
    db.commit()
    return ok(result, request)
