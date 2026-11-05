"""品牌与门户服务（v3.67）：一份品牌包 + 平台级逐人授权。

口径：
- 公开读（登录页要用）：只出展示字段，不出域名清单、操作者、内部键；
- 写入：``branding:manage``，默认只有 platform_admin，可经 ``platform_grants`` 逐人授权；
- 域名：只做规范化与记录（生成链接、非官方入口提示），应用不配 DNS/证书；
- logo：只收 PNG/JPG/WEBP（**不收 SVG**，避免登录页 inline 出图造成存储型 XSS）。
"""

from __future__ import annotations

import os
import re
from datetime import UTC
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.audit.service import log_audit
from app.modules.branding.models import (
    DEFAULT_BRANDING_ID,
    PlatformBranding,
    PlatformGrant,
)
from app.modules.chat.attachments import EXT_FAMILY, IMAGE_MIMES, _sniff
from app.modules.knowledge.storage import LocalStorageBackend
from app.modules.yards.permissions import PLATFORM_PERMISSIONS

log = get_logger(__name__)

DEFAULT_APP_NAME = "科技小院"
DEFAULT_SHORT_NAME = "科技小院"
DEFAULT_TAGLINE = "多智能体自生长开发平台"
DEFAULT_FOOTER = "由 科技小院 · 多智能体平台 生成"
DEFAULT_PRIMARY_COLOR = "#6366F1"

#: logo 上限（MB）。品牌图是界面上的一块小图，不按附件 25MB 收
LOGO_MAX_MB = 1
#: 门户域名最多记 5 个（主域名排第一）
MAX_DOMAINS = 5
#: 文本字段长度上限（与 ORM 列一致，避免截断式静默丢字）
FIELD_LIMITS = {
    "app_name": 40,
    "short_name": 8,
    "login_tagline": 40,
    "footer_text": 80,
}
#: 对外展示的 logo 路径（相对 API base，前端用 apiUrl() 拼）
LOGO_PATH = "/branding/logo"

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_IPV4_RE = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)$"
)


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


def get_row(db: Session) -> PlatformBranding | None:
    return db.get(PlatformBranding, DEFAULT_BRANDING_ID)


def ensure_row(db: Session) -> PlatformBranding:
    """取品牌行，没有就建一条空行（空值在出参里按默认文案补齐）。"""
    row = get_row(db)
    if row is None:
        row = PlatformBranding(
            id=DEFAULT_BRANDING_ID,
            app_name="",
            short_name="",
            login_tagline="",
            footer_text="",
            primary_color="",
            domains_json=[],
            updated_by="",
        )
        db.add(row)
        db.flush()
    return row


def _version(row: PlatformBranding | None) -> int:
    if row is None or row.updated_at is None:
        return 0
    stamp = row.updated_at if row.updated_at.tzinfo else row.updated_at.replace(tzinfo=UTC)
    return int(stamp.timestamp())


def _resolved(row: PlatformBranding | None) -> dict[str, Any]:
    app_name = (row.app_name if row else "") or DEFAULT_APP_NAME
    return {
        "app_name": app_name,
        "short_name": (row.short_name if row else "") or DEFAULT_SHORT_NAME,
        "login_tagline": (row.login_tagline if row else "") or DEFAULT_TAGLINE,
        "footer_text": (row.footer_text if row else "") or DEFAULT_FOOTER,
        "primary_color": (row.primary_color if row else "") or DEFAULT_PRIMARY_COLOR,
        "logo_url": LOGO_PATH if (row and row.logo_key) else None,
        "brand_version": _version(row),
    }


def public_payload(db: Session) -> dict[str, Any]:
    """匿名可读：只出展示字段与 logo 地址。"""
    return _resolved(get_row(db))


def admin_payload(db: Session) -> dict[str, Any]:
    """管理视图：展示字段 + 原始值 + 域名 + 操作者（供设置页回填）。"""
    row = get_row(db)
    payload = _resolved(row)
    payload.update(
        {
            "domains": list(row.domains_json or []) if row else [],
            "raw": {
                "app_name": row.app_name if row else "",
                "short_name": row.short_name if row else "",
                "login_tagline": row.login_tagline if row else "",
                "footer_text": row.footer_text if row else "",
                "primary_color": row.primary_color if row else "",
            },
            "defaults": {
                "app_name": DEFAULT_APP_NAME,
                "short_name": DEFAULT_SHORT_NAME,
                "login_tagline": DEFAULT_TAGLINE,
                "footer_text": DEFAULT_FOOTER,
                "primary_color": DEFAULT_PRIMARY_COLOR,
            },
            "max_domains": MAX_DOMAINS,
            "logo_max_mb": LOGO_MAX_MB,
            "updated_by": row.updated_by if row else "",
            "updated_at": row.updated_at.isoformat() if (row and row.updated_at) else None,
        }
    )
    return payload


# ---------------------------------------------------------------------------
# 校验与写入
# ---------------------------------------------------------------------------


def normalize_domain(raw: str) -> str:
    """把用户粘贴的地址规范化成 host：去协议/路径/端口、转小写、IDN 转 punycode。"""
    value = str(raw or "").strip().lower()
    if not value:
        return ""
    if "://" in value:
        value = urlsplit(value).netloc or value.split("://", 1)[1]
    value = value.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    value = value.rsplit("@", 1)[-1]
    if value.count(":") == 1:  # host:port（IPv6 不在支持范围）
        host, _, port = value.partition(":")
        if port.isdigit():
            value = host
    if not value:
        return ""
    if value == "localhost" or _IPV4_RE.match(value):
        return value
    try:
        value = value.encode("idna").decode("ascii")
    except UnicodeError as e:
        raise AppError("INVALID_REQUEST", f"域名无法识别：{raw}", http_status=400) from e
    if not _HOST_RE.match(value):
        raise AppError(
            "INVALID_REQUEST",
            f"域名格式不合法：{raw}（示例：portal.example.edu.cn）",
            http_status=400,
        )
    return value


def normalize_color(raw: str) -> str:
    """主色：空串=恢复默认；支持 #RGB 展开成 #RRGGBB。"""
    value = str(raw or "").strip()
    if not value:
        return ""
    if re.match(r"^#[0-9a-fA-F]{3}$", value):
        value = "#" + "".join(ch * 2 for ch in value[1:])
    if not _COLOR_RE.match(value):
        raise AppError("INVALID_REQUEST", "主色需为 #RRGGBB 格式。", http_status=400)
    return value.upper()


def normalize_domains(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        raise AppError("INVALID_REQUEST", "域名必须是数组。", http_status=400)
    cleaned = [normalize_domain(str(item)) for item in raw]
    kept = [item for item in dict.fromkeys(cleaned) if item]
    if len(kept) > MAX_DOMAINS:
        raise AppError("INVALID_REQUEST", f"最多记录 {MAX_DOMAINS} 个域名。", http_status=400)
    return kept


def update_settings(
    db: Session,
    *,
    actor_id: str,
    request_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """更新品牌文案/主色/域名。空串表示「恢复默认」。"""
    row = ensure_row(db)
    changed: list[str] = []
    for field, limit in FIELD_LIMITS.items():
        if field not in payload:
            continue
        raw = payload[field]
        if not isinstance(raw, str):
            raise AppError("INVALID_REQUEST", f"{field} 必须是文本。", http_status=400)
        text = raw.strip()
        if len(text) > limit:
            raise AppError("INVALID_REQUEST", f"{field} 最多 {limit} 个字。", http_status=400)
        setattr(row, field, text)
        changed.append(field)
    if "primary_color" in payload:
        row.primary_color = normalize_color(str(payload.get("primary_color") or ""))
        changed.append("primary_color")
    if "domains" in payload:
        row.domains_json = normalize_domains(payload.get("domains"))
        changed.append("domains")
    if not changed:
        raise AppError("INVALID_REQUEST", "没有可更新的字段。", http_status=400)
    row.updated_by = actor_id
    db.flush()
    log_audit(
        db,
        yard_id=None,
        actor_id=actor_id,
        action="branding_updated",
        resource_type="platform_branding",
        resource_id=row.id,
        result="success",
        request_id=request_id,
        metadata={"fields": changed},
    )
    return admin_payload(db)


def set_logo(db: Session, *, actor_id: str, request_id: str, file: UploadFile) -> dict[str, Any]:
    """替换 logo：校验 → 落新文件 → 写键 → 删旧文件（失败不留孤儿）。"""
    filename = os.path.basename(str(file.filename or "")).strip()[:255]
    ext = os.path.splitext(filename)[1].lower()
    if ext not in IMAGE_MIMES:
        raise AppError(
            "UNSUPPORTED_FILE_TYPE",
            f"logo 只支持 PNG/JPG/WEBP，收到：{ext or '未知'}。",
            http_status=415,
        )
    data = file.file.read()
    if not data:
        raise AppError("INVALID_REQUEST", "上传的文件为空。", http_status=400)
    if len(data) > LOGO_MAX_MB * 1024 * 1024:
        raise AppError("FILE_TOO_LARGE", f"logo 过大，上限 {LOGO_MAX_MB}MB。", http_status=413)
    signature = _sniff(data)
    if signature is None or signature.family != EXT_FAMILY.get(ext):
        raise AppError(
            "UNSUPPORTED_FILE_TYPE", "文件内容与扩展名不一致，已拒绝上传。", http_status=415
        )

    row = ensure_row(db)
    storage = LocalStorageBackend()
    old_key = row.logo_key
    row.logo_key = storage.save(data, ext)
    row.logo_mime = IMAGE_MIMES[ext]
    row.updated_by = actor_id
    if old_key:
        try:
            storage.delete(old_key)
        except Exception as e:  # noqa: BLE001 —— 清理尽力而为，不能让上传失败
            log.warning("旧 logo 删除失败 key=%s: %s", old_key, e)
    db.flush()
    log_audit(
        db,
        yard_id=None,
        actor_id=actor_id,
        action="branding_logo_changed",
        resource_type="platform_branding",
        resource_id=row.id,
        result="success",
        request_id=request_id,
        metadata={"mime": row.logo_mime},
    )
    return admin_payload(db)


def clear_logo(db: Session, *, actor_id: str, request_id: str) -> dict[str, Any]:
    row = ensure_row(db)
    if row.logo_key:
        try:
            LocalStorageBackend().delete(row.logo_key)
        except Exception as e:  # noqa: BLE001 —— 行已清空，文件残留只是垃圾
            log.warning("logo 删除失败 key=%s: %s", row.logo_key, e)
    row.logo_key = None
    row.logo_mime = None
    row.updated_by = actor_id
    db.flush()
    log_audit(
        db,
        yard_id=None,
        actor_id=actor_id,
        action="branding_logo_changed",
        resource_type="platform_branding",
        resource_id=row.id,
        result="success",
        request_id=request_id,
        metadata={"cleared": True},
    )
    return admin_payload(db)


def read_logo(db: Session) -> tuple[bytes, str, int]:
    """取 logo 字节、MIME 与版本号（ETag 用）。未设置时 404，前端回落文字 logo。"""
    row = get_row(db)
    if row is None or not row.logo_key:
        raise AppError("BRANDING_LOGO_MISSING", "尚未设置门户 logo。", http_status=404)
    try:
        with LocalStorageBackend().open(row.logo_key) as fh:
            data = fh.read()
    except FileNotFoundError as e:
        raise AppError(
            "BRANDING_LOGO_MISSING", "logo 文件已丢失，请重新上传。", http_status=404
        ) from e
    return data, row.logo_mime or "application/octet-stream", _version(row)


# ---------------------------------------------------------------------------
# 平台级授权（branding:manage）
# ---------------------------------------------------------------------------


def platform_grants(db: Session, user_id: str) -> frozenset[str]:
    row = db.scalar(select(PlatformGrant).where(PlatformGrant.user_id == user_id))
    if row is None or not isinstance(row.grants_json, list):
        return frozenset()
    return frozenset({str(g) for g in row.grants_json} & PLATFORM_PERMISSIONS)


def platform_permissions(
    db: Session, user_id: str, *, is_platform_admin: bool = False
) -> frozenset[str]:
    """平台权限：超管直通；其他人按 platform_grants（与 JWT/小院角色无关）。"""
    if is_platform_admin:
        return PLATFORM_PERMISSIONS
    return platform_grants(db, user_id)


def can_manage_branding(db: Session, user: Any) -> bool:
    if "platform_admin" in (getattr(user, "roles", None) or []):
        return True
    return "branding:manage" in platform_grants(db, getattr(user, "user_id", ""))


def list_grant_targets(db: Session) -> list[dict[str, Any]]:
    """授权面板用：活跃用户 + 当前平台授权。"""
    from app.modules.auth.models import User

    users = list(
        db.scalars(select(User).where(User.status == "active").order_by(User.username)).all()
    )
    return [
        {
            "user_id": u.id,
            "username": u.username,
            "display_name": u.display_name or u.username,
            "grants": sorted(platform_grants(db, u.id)),
        }
        for u in users
    ]


def set_user_grants(
    db: Session, *, actor_id: str, request_id: str, user_id: str, grants: list[Any]
) -> dict[str, Any]:
    """覆盖式写入某用户的平台授权；空列表即撤销。"""
    requested = {str(g) for g in grants}
    invalid = requested - PLATFORM_PERMISSIONS
    if invalid:
        raise AppError(
            "INVALID_REQUEST",
            "不支持的平台权限：" + "、".join(sorted(invalid)),
            http_status=400,
        )
    row = db.scalar(select(PlatformGrant).where(PlatformGrant.user_id == user_id))
    if not requested:
        if row is not None:
            db.delete(row)
    elif row is None:
        db.add(PlatformGrant(user_id=user_id, grants_json=sorted(requested)))
    else:
        row.grants_json = sorted(requested)
    db.flush()
    log_audit(
        db,
        yard_id=None,
        actor_id=actor_id,
        action="platform_grant_updated",
        resource_type="platform_grant",
        resource_id=user_id,
        result="success",
        request_id=request_id,
        metadata={"grants": sorted(requested)},
    )
    return {"user_id": user_id, "grants": sorted(requested)}


__all__ = [
    "DEFAULT_APP_NAME",
    "DEFAULT_FOOTER",
    "DEFAULT_PRIMARY_COLOR",
    "DEFAULT_SHORT_NAME",
    "DEFAULT_TAGLINE",
    "LOGO_MAX_MB",
    "LOGO_PATH",
    "MAX_DOMAINS",
    "admin_payload",
    "can_manage_branding",
    "clear_logo",
    "ensure_row",
    "get_row",
    "list_grant_targets",
    "normalize_color",
    "normalize_domain",
    "normalize_domains",
    "platform_grants",
    "platform_permissions",
    "public_payload",
    "read_logo",
    "set_logo",
    "set_user_grants",
    "update_settings",
]
