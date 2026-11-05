"""品牌与门户 REST API（/branding、/platform/grants，v3.67）。

权限口径：
- ``GET /branding`` 与 ``GET /branding/logo`` **匿名可读**——登录页要用品牌，
  此时还没有令牌；只出展示字段，不出域名清单/操作者/存储键；
- 其余端点要 ``branding:manage``：platform_admin 直通，或由超管在
  ``platform_grants`` 里逐人授予（小院角色拿不到，也不能经小院授权下放）；
- ``/platform/grants`` 严格 platform_admin，被授权者不能再转授。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, get_db_session
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.branding import service
from app.modules.yards.permissions import PLATFORM_PERMISSIONS

router = APIRouter(tags=["branding"])


class BrandingUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_name: str | None = Field(default=None, max_length=40)
    short_name: str | None = Field(default=None, max_length=8)
    login_tagline: str | None = Field(default=None, max_length=40)
    footer_text: str | None = Field(default=None, max_length=80)
    primary_color: str | None = Field(default=None, max_length=7)
    domains: list[str] | None = Field(default=None, max_length=service.MAX_DOMAINS)


class PlatformGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grants: list[str] = Field(default_factory=list, max_length=len(PLATFORM_PERMISSIONS))


def require_branding_manage(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> CurrentUser:
    if not service.can_manage_branding(db, user):
        raise AppError(
            "FORBIDDEN",
            "只有平台管理员或被授权的管理员可修改品牌与门户。",
            http_status=403,
        )
    return user


def require_platform_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if "platform_admin" not in (user.roles or []):
        raise AppError("FORBIDDEN", "只有平台超级管理员可授予品牌管理权限。", http_status=403)
    return user


@router.get("/branding")
def public_branding(request: Request, db: Session = Depends(get_db_session)):
    """品牌解析（匿名）：登录页、共享页与启动时都用它。"""
    return ok(service.public_payload(db), request)


@router.get("/branding/logo")
def branding_logo(request: Request, db: Session = Depends(get_db_session)):
    """门户 logo 字节（匿名）：带 ETag，未设置返回 404 让前端回落文字 logo。"""
    data, mime, version = service.read_logo(db)
    etag = f'W/"{version}"'
    headers = {"Cache-Control": "public, max-age=300", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=data, media_type=mime, headers=headers)


@router.get("/branding/settings")
def branding_settings(
    request: Request,
    user: CurrentUser = Depends(require_branding_manage),
    db: Session = Depends(get_db_session),
):
    """品牌管理视图（含域名与操作者，仅管理员可见）。"""
    return ok(service.admin_payload(db), request)


@router.put("/branding/settings")
def update_branding_settings(
    payload: BrandingUpdateRequest,
    request: Request,
    user: CurrentUser = Depends(require_branding_manage),
    db: Session = Depends(get_db_session),
):
    try:
        out = service.update_settings(
            db,
            actor_id=user.user_id,
            request_id=user.request_id,
            payload=payload.model_dump(exclude_none=True),
        )
        db.commit()
    except AppError:
        db.rollback()
        raise
    return ok(out, request)


@router.post("/branding/logo")
def upload_branding_logo(
    request: Request,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_branding_manage),
    db: Session = Depends(get_db_session),
):
    """上传 logo（PNG/JPG/WEBP，≤1MB，文件头须与扩展名一致）。"""
    try:
        out = service.set_logo(db, actor_id=user.user_id, request_id=user.request_id, file=file)
        db.commit()
    except AppError:
        db.rollback()
        raise
    return ok(out, request)


@router.delete("/branding/logo")
def delete_branding_logo(
    request: Request,
    user: CurrentUser = Depends(require_branding_manage),
    db: Session = Depends(get_db_session),
):
    try:
        out = service.clear_logo(db, actor_id=user.user_id, request_id=user.request_id)
        db.commit()
    except AppError:
        db.rollback()
        raise
    return ok(out, request)


@router.get("/platform/grants")
def list_platform_grants(
    request: Request,
    user: CurrentUser = Depends(require_platform_admin),
    db: Session = Depends(get_db_session),
):
    """授权面板：活跃用户 + 当前平台授权。"""
    return ok(
        {
            "items": service.list_grant_targets(db),
            "grantable": sorted(PLATFORM_PERMISSIONS),
        },
        request,
    )


@router.put("/platform/grants/{user_id}")
def update_platform_grants(
    user_id: str,
    payload: PlatformGrantRequest,
    request: Request,
    user: CurrentUser = Depends(require_platform_admin),
    db: Session = Depends(get_db_session),
):
    """覆盖式写入某用户的平台授权；空列表即撤销。"""
    try:
        out = service.set_user_grants(
            db,
            actor_id=user.user_id,
            request_id=user.request_id,
            user_id=user_id,
            grants=payload.grants,
        )
        db.commit()
    except AppError:
        db.rollback()
        raise
    return ok(out, request)
