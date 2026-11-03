"""官方技能库/插件库接口（v3.35）。

权限：统一走 ``require_roles("platform_admin","admin","yard_admin","yard_owner")``；
官方源（yard_id 为空）的编辑/删除在 service 层再收口为平台管理员。
安装/回滚写审计；响应只回传公钥指纹，绝不回传私钥或原始密钥。
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.deps import (
    CurrentUser,
    YardContext,
    get_db_session,
    get_yard_context,
    require_roles,
)
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.library import service
from app.modules.library.schemas import (
    AutoUpdateRequest,
    ItemInstallRequest,
    PinRequest,
    SourceCreateRequest,
    SourceSyncRequest,
    SourceUpdateRequest,
)

router = APIRouter(prefix="/library", tags=["library"])

_library_gate = require_roles("platform_admin", "yard_admin", "yard_owner")


def _is_platform_admin(user: CurrentUser) -> bool:
    return "platform_admin" in set(user.roles or [])


# ---------------------------------------------------------------------------
# 源
# ---------------------------------------------------------------------------


@router.get("/sources")
def get_sources(
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_library_gate),
    db: Session = Depends(get_db_session),
):
    """可见源：官方源 + 本小院私有源。"""
    return ok(service.list_sources(db, ctx, is_platform_admin=_is_platform_admin(user)), request)


@router.post("/sources")
def create_source(
    request: Request,
    payload: SourceCreateRequest,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_library_gate),
    db: Session = Depends(get_db_session),
):
    """新建源：平台管理员可建官方源；小院管理员只能建本小院私有源。"""
    try:
        result = service.create_source(db, ctx, payload, is_platform_admin=_is_platform_admin(user))
        db.commit()
    except AppError:
        db.rollback()
        raise
    return ok(result, request)


@router.patch("/sources/{source_id}")
def patch_source(
    request: Request,
    source_id: str,
    payload: SourceUpdateRequest,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_library_gate),
    db: Session = Depends(get_db_session),
):
    """更新源：官方源仅平台管理员，私有源仅本小院。"""
    try:
        result = service.update_source(
            db, ctx, source_id, payload, is_platform_admin=_is_platform_admin(user)
        )
        db.commit()
    except AppError:
        db.rollback()
        raise
    return ok(result, request)


@router.delete("/sources/{source_id}")
def remove_source(
    request: Request,
    source_id: str,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_library_gate),
    db: Session = Depends(get_db_session),
):
    """删除源：仍有安装台账时拒绝（撤销条目即可，不删已装数据）。"""
    try:
        result = service.delete_source(
            db, ctx, source_id, is_platform_admin=_is_platform_admin(user)
        )
        db.commit()
    except AppError:
        db.rollback()
        raise
    return ok(result, request)


@router.post("/sources/{source_id}/sync")
def sync_source(
    request: Request,
    source_id: str,
    body: SourceSyncRequest | None = Body(default=None),
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_library_gate),
    db: Session = Depends(get_db_session),
):
    """拉取清单：ETag/sequence 条件跳过；失败落账后仍返回统一错误信封。"""
    try:
        result = service.sync_source(
            db,
            ctx.yard_id,
            source_id,
            force=bool(body.force) if isinstance(body, SourceSyncRequest) else False,
        )
        db.commit()
    except AppError:
        # 失败运行记录由 service 写入，提交后错误链路仍可回放
        db.commit()
        raise
    except Exception:
        db.rollback()
        raise
    return ok(result, request)


# ---------------------------------------------------------------------------
# 条目
# ---------------------------------------------------------------------------


@router.get("/items")
def get_items(
    request: Request,
    source_id: str | None = Query(default=None),
    item_type: str | None = Query(default=None),
    keyword: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=200),
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_library_gate),
    db: Session = Depends(get_db_session),
):
    """条目列表：只列可见源；同一 item_key 多版本按版本号倒序。"""
    if item_type and item_type not in service.ITEM_TYPES:
        raise AppError("INVALID_REQUEST", "item_type 不合法。", http_status=400)
    return ok(
        service.list_items(
            db,
            ctx,
            source_id=source_id,
            item_type=item_type,
            keyword=keyword,
            limit=limit,
        ),
        request,
    )


@router.post("/items/{item_id}/install")
def install_library_item(
    request: Request,
    item_id: str,
    payload: ItemInstallRequest | None = Body(default=None),
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_library_gate),
    db: Session = Depends(get_db_session),
):
    """安装：下载→重算 sha256→验签验包→复用 Skill/Plugin 落地；同版本幂等。"""
    version = payload.version if isinstance(payload, ItemInstallRequest) else None
    try:
        result = service.install_item(db, ctx, item_id, version=version)
        db.commit()
    except AppError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    return ok(result, request)


# ---------------------------------------------------------------------------
# 权限授权与更新中心（v3.59）
# ---------------------------------------------------------------------------


@router.get("/updates")
def get_updates(
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_library_gate),
    db: Session = Depends(get_db_session),
):
    """更新中心：可更新条目、风险分布与能否自动更新。"""
    return ok(service.list_updates(db, ctx), request)


@router.get("/consents")
def get_consents(
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_library_gate),
    db: Session = Depends(get_db_session),
):
    """本小院已授予的插件权限（按插件取最新一条）。"""
    return ok(service.list_plugin_consents(db, ctx), request)


@router.delete("/consents/{consent_id}")
def revoke_consent(
    request: Request,
    consent_id: str,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_library_gate),
    db: Session = Depends(get_db_session),
):
    """撤销授权：保留历史，仅把当前授权标记为失效。"""
    try:
        result = service.revoke_plugin_consent(db, ctx, consent_id)
        db.commit()
    except AppError:
        db.rollback()
        raise
    return ok(result, request)


@router.get("/items/{item_id}/consent")
def get_item_consent(
    request: Request,
    item_id: str,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_library_gate),
    db: Session = Depends(get_db_session),
):
    """安装前预览：该条目需要哪些权限、风险等级、当前是否已授权。"""
    try:
        result = service.consent_preview(db, ctx, item_id)
    except AppError:
        raise
    except Exception:
        db.rollback()
        raise
    return ok(result, request)


@router.post("/items/{item_id}/consent")
def grant_item_consent(
    request: Request,
    item_id: str,
    payload: dict | None = Body(default=None),
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_library_gate),
    db: Session = Depends(get_db_session),
):
    """记录授权：必须显式 ack=true，授权后同一权限范围可安装/升级。"""
    ack = bool((payload or {}).get("ack"))
    if not ack:
        raise AppError(
            "PLUGIN_CONSENT_ACK_REQUIRED",
            "请先勾选并确认插件权限说明。",
            http_status=400,
        )
    try:
        result = service.grant_consent(db, ctx, item_id)
        db.commit()
    except AppError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    return ok(result, request)


# ---------------------------------------------------------------------------
# 安装台账
# ---------------------------------------------------------------------------


@router.get("/installs")
def get_installs(
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_library_gate),
    db: Session = Depends(get_db_session),
):
    """本小院安装台账：含可更新数量与回滚可用性。"""
    return ok(service.list_installs(db, ctx), request)


@router.post("/installs/{install_id}/update")
def update_library_item(
    request: Request,
    install_id: str,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_library_gate),
    db: Session = Depends(get_db_session),
):
    """更新到最新可用版本；失败回滚事务，旧版本保留。"""
    try:
        result = service.update_item(db, ctx, install_id)
        db.commit()
    except AppError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    return ok(result, request)


@router.post("/installs/{install_id}/rollback")
def rollback_library_item(
    request: Request,
    install_id: str,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_library_gate),
    db: Session = Depends(get_db_session),
):
    """离线回滚到上一版本（仅 Skill：本地版本表仍在）。"""
    try:
        result = service.rollback_item(db, ctx, install_id)
        db.commit()
    except AppError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    return ok(result, request)


@router.post("/installs/{install_id}/pin")
def pin_library_item(
    request: Request,
    install_id: str,
    payload: PinRequest | None = Body(default=None),
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_library_gate),
    db: Session = Depends(get_db_session),
):
    """固定版本：pinned 条目永不自动更新。"""
    pinned = payload.pinned if isinstance(payload, PinRequest) else True
    try:
        result = service.pin_item(db, ctx, install_id, pinned=pinned)
        db.commit()
    except AppError:
        db.rollback()
        raise
    return ok(result, request)


@router.post("/installs/{install_id}/auto-update")
def auto_update_library_item(
    request: Request,
    install_id: str,
    payload: AutoUpdateRequest | None = Body(default=None),
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_library_gate),
    db: Session = Depends(get_db_session),
):
    """开关自动更新（只影响低风险版本）。"""
    enabled = payload.value if isinstance(payload, AutoUpdateRequest) else True
    try:
        result = service.set_auto_update(db, ctx, install_id, enabled=enabled)
        db.commit()
    except AppError:
        db.rollback()
        raise
    return ok(result, request)


@router.get("/status")
def library_status(
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_library_gate),
    db: Session = Depends(get_db_session),
):
    """源状态、上次同步、可更新数量与离线标记。"""
    return ok(
        service.status(db, ctx, is_platform_admin=_is_platform_admin(user)),
        request,
    )
