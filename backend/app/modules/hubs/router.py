"""枢纽 REST API（/hubs，v3.65）：通用基座上的运营模式选择。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.deps import (
    CurrentUser,
    YardContext,
    get_current_user,
    get_db_session,
    get_yard_context,
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


class HubMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=2, max_length=36)
    role: str = Field(pattern="^(manager|viewer)$")


@router.get("")
def list_hubs(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """我可见的枢纽：platform_admin 全部；普通用户按 hub_members。"""
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
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """创建枢纽并选择运营模式（创建者自动成为 manager）。"""
    if "platform_admin" not in (user.roles or []) and not user.yard_id:
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    hub = service.create_hub(
        db,
        ctx,
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
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """当前用户可绑定到枢纽的小院清单（platform_admin 全部；普通用户为其管理的小院）。"""
    return ok(service.list_attachable_yards(db, user), request)


@router.get("/{hub_id}/overview")
def hub_overview(
    hub_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """枢纽工作台：所辖小院聚合（只出数字，不含日志内容）。"""
    return ok(service.hub_overview(db, ctx, hub_id), request)


@router.post("/{hub_id}/yards")
def attach_yard(
    hub_id: str,
    payload: HubAttachRequest,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    result = service.attach_yard(db, ctx, hub_id, payload.yard_id)
    db.commit()
    return ok(result, request)


@router.delete("/yards/{yard_id}")
def detach_yard(
    yard_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    result = service.detach_yard(db, ctx, yard_id)
    db.commit()
    return ok(result, request)


@router.get("/{hub_id}/member-candidates")
def member_candidates(
    hub_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """可添加为枢纽成员的活跃用户清单（已在本枢纽的排除）。"""
    service.require_hub_access(db, ctx, hub_id, manager=True)
    return ok(service.member_candidates(db, ctx, hub_id), request)


@router.post("/{hub_id}/members")
def add_member(
    hub_id: str,
    payload: HubMemberRequest,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    result = service.add_member(db, ctx, hub_id, payload.user_id, payload.role)
    db.commit()
    return ok(result, request)
