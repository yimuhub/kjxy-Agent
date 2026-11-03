"""官方供应商通道路由（v3.33）：``/llm/relay/channels*``。

权限：platform_admin/admin/yard_admin/yard_owner（复用供应商域角色闸门）。
小院管理员只能看/改本小院通道；平台级（yard_id 为 NULL）通道仅平台管理员可写。
密钥只入参，出参一律 ``api_key_masked``；所有写操作记审计且 metadata 不含密钥。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_db_session, require_roles
from app.core.responses import ok
from app.modules.audit.service import log_audit
from app.modules.llm import relay_service
from app.modules.llm.supplier_schemas import (
    RelayChannelCreate,
    RelayChannelUpdate,
)

router = APIRouter(tags=["llm-relay"])

_admin_only = require_roles("platform_admin", "yard_admin", "yard_owner")


def _ctx(user: CurrentUser = Depends(_admin_only)) -> tuple[CurrentUser, bool]:
    return user, ("platform_admin" in user.roles)


def _audit(db: Session, user: CurrentUser, action: str, channel, meta: dict) -> None:
    log_audit(
        db,
        yard_id=user.yard_id,
        actor_id=user.user_id,
        action=action,
        resource_type="relay_channel",
        resource_id=getattr(channel, "id", channel if isinstance(channel, str) else None),
        result="success",
        request_id=user.request_id,
        metadata=meta,
    )


def _meta(out) -> dict:
    return {
        "channel_id": out.id,
        "name": out.name,
        "kind": out.kind,
        "enabled": out.enabled,
    }


@router.get("/relay/channels", response_model=None)
def list_relay_channels(
    ctx=Depends(_ctx),
    db=Depends(get_db_session),
):
    user, is_platform_admin = ctx
    items = relay_service.list_channels(db, user.yard_id, is_platform_admin=is_platform_admin)
    return ok([item.model_dump() for item in items])


@router.post("/relay/channels", response_model=None)
def create_relay_channel(
    payload: RelayChannelCreate,
    ctx=Depends(_ctx),
    db=Depends(get_db_session),
):
    user, is_platform_admin = ctx
    try:
        out = relay_service.create_channel(
            db, user.yard_id, payload, is_platform_admin=is_platform_admin
        )
        _audit(db, user, "relay_channel_create", out, _meta(out))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(out.model_dump())


@router.patch("/relay/channels/{channel_id}", response_model=None)
def update_relay_channel(
    channel_id: str,
    payload: RelayChannelUpdate,
    ctx=Depends(_ctx),
    db=Depends(get_db_session),
):
    user, is_platform_admin = ctx
    try:
        out = relay_service.update_channel(
            db, user.yard_id, channel_id, payload, is_platform_admin=is_platform_admin
        )
        _audit(db, user, "relay_channel_update", out, _meta(out))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(out.model_dump())


@router.delete("/relay/channels/{channel_id}", response_model=None)
def delete_relay_channel(
    channel_id: str,
    ctx=Depends(_ctx),
    db=Depends(get_db_session),
):
    user, is_platform_admin = ctx
    try:
        relay_service.delete_channel(
            db, user.yard_id, channel_id, is_platform_admin=is_platform_admin
        )
        _audit(db, user, "relay_channel_delete", channel_id, {"channel_id": channel_id})
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok({"deleted": channel_id})


@router.post("/relay/channels/{channel_id}/sync", response_model=None)
async def sync_relay_channel(
    channel_id: str,
    ctx=Depends(_ctx),
    db=Depends(get_db_session),
):
    user, is_platform_admin = ctx
    try:
        result = await relay_service.sync_relay_catalog(
            db, user.yard_id, channel_id, is_platform_admin=is_platform_admin
        )
        _audit(
            db,
            user,
            "relay_channel_sync",
            channel_id,
            {
                "channel_id": channel_id,
                "supplier_id": result.supplier_id,
                "created": result.created,
                "updated": result.updated,
                "removed": result.removed,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(result.model_dump())


@router.get("/relay/channels/{channel_id}/models", response_model=None)
def list_relay_channel_models(
    channel_id: str,
    ctx=Depends(_ctx),
    db=Depends(get_db_session),
):
    user, _is_platform_admin = ctx
    items = relay_service.list_channel_models(db, user.yard_id, channel_id)
    return ok([item.model_dump() for item in items])
