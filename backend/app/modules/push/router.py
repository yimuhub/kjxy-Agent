"""推送通道 HTTP 接口：``/settings/push-channels``（tenant_admin 专属 CRUD）。

安全口径：
- URL 提交后响应只回掩码（``url_masked``），**无轮换端点，重填 URL 即换**；
- 保存/换 URL 时 SSRF 校验（localhost/元数据等直接 400 SSRF_BLOCKED）；
- 跨租户与不存在统一 404；同租户重名 409；审计只记 kind/events，绝不记 URL。
"""

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import TenantContext, get_db_session, get_tenant_context
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.audit.service import log_audit
from app.modules.push import service
from app.modules.push.models import PushChannel
from app.modules.push.schemas import PushChannelCreate, PushChannelUpdate

router = APIRouter(prefix="/settings", tags=["push"])

_ADMIN_ROLES = {"platform_admin", "admin", "tenant_admin"}


def _admin(ctx: TenantContext = Depends(get_tenant_context)) -> TenantContext:
    if not set(ctx.roles) & _ADMIN_ROLES:
        raise AppError("FORBIDDEN", "推送通道管理需要租户管理员权限。", http_status=403)
    return ctx


def _audit(ctx: TenantContext, db: Session, action: str, resource_id: str, meta: dict) -> None:
    log_audit(
        db,
        tenant_id=ctx.tenant_id,
        actor_id=ctx.user_id,
        action=action,
        resource_type="push_channel",
        resource_id=resource_id,
        result="success",
        request_id=ctx.request_id,
        metadata=meta,
    )


@router.get("/push-channels", response_model=None)
def list_channels(
    ctx: TenantContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    rows = list(
        db.scalars(
            select(PushChannel)
            .where(PushChannel.tenant_id == ctx.tenant_id)
            .order_by(PushChannel.created_at.desc())
        )
    )
    return ok([service.channel_to_out(ch) for ch in rows])


@router.post("/push-channels", response_model=None)
def create_channel(
    payload: PushChannelCreate,
    ctx: TenantContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    service.validate_kind(payload.kind)
    service.validate_events(payload.events)
    dup = db.scalars(
        select(PushChannel.id).where(
            PushChannel.tenant_id == ctx.tenant_id, PushChannel.name == payload.name
        )
    ).first()
    if dup:
        raise AppError("DUPLICATE_NAME", "同租户下已存在同名推送通道。", http_status=409)
    ch = PushChannel(
        tenant_id=ctx.tenant_id,
        name=payload.name,
        kind=payload.kind,
        url_enc=service.prepare_url_enc(payload.url) if payload.url else None,
        events_json=list(payload.events),
        enabled=payload.enabled,
        created_by=ctx.user_id,
    )
    db.add(ch)
    db.flush()
    # 审计 metadata 只记 kind/events——URL 含 key，绝不入审计
    _audit(ctx, db, "push_channel_create", ch.id, {"kind": ch.kind, "events": ch.events_json})
    db.commit()
    return ok(service.channel_to_out(ch))


@router.patch("/push-channels/{channel_id}", response_model=None)
def update_channel(
    channel_id: str,
    payload: PushChannelUpdate,
    ctx: TenantContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    ch = service.get_channel_or_404(db, ctx.tenant_id, channel_id)
    changes: dict[str, Any] = payload.model_dump(exclude_unset=True)
    if "kind" in changes and changes["kind"] is not None:
        service.validate_kind(str(changes["kind"]))
    if "events" in changes and changes["events"] is not None:
        service.validate_events([str(e) for e in changes["events"]])
    new_url = changes.pop("url", None)
    if new_url:
        # 重填即换：SSRF 校验失败即 400 且旧 URL 保留；换成功 = 重新开始（失败计数清零）
        ch.url_enc = service.prepare_url_enc(str(new_url))
        ch.consecutive_failures = 0
    if "name" in changes and changes["name"] is not None:
        new_name = str(changes["name"])
        if new_name != ch.name:
            dup = db.scalars(
                select(PushChannel.id).where(
                    PushChannel.tenant_id == ctx.tenant_id,
                    PushChannel.name == new_name,
                    PushChannel.id != ch.id,
                )
            ).first()
            if dup:
                raise AppError("DUPLICATE_NAME", "同租户下已存在同名推送通道。", http_status=409)
    for field, value in changes.items():
        setattr(ch, field, value)
    _audit(
        ctx,
        db,
        "push_channel_update",
        ch.id,
        {"fields": sorted([*changes, *(["url"] if new_url else [])])},
    )
    db.commit()
    return ok(service.channel_to_out(ch))


@router.delete("/push-channels/{channel_id}", response_model=None)
def delete_channel(
    channel_id: str,
    ctx: TenantContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    ch = service.get_channel_or_404(db, ctx.tenant_id, channel_id)
    db.delete(ch)
    _audit(ctx, db, "push_channel_delete", channel_id, {"kind": ch.kind})
    db.commit()
    return ok({"deleted": channel_id})
