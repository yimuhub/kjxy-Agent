"""媒体产物存储与签名取流（v3.73）。

口径与 ``growth/poster.py`` 一致：随机存储键 + HMAC capability URL，``<img>``/
``<video>`` 直接加载不带 Authorization 头也能取；只读 ``media/`` 子目录，
与知识库、附件、海报目录互不越界。

与海报的差别只有两点：签名原文加 ``media|`` 前缀（两域 URL 不可互换），
以及支持 HTTP Range（视频拖动进度条必须 206）。
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

from app.core.config import settings
from app.modules.knowledge.storage import LocalStorageBackend

MEDIA_DIR = "media"

_MIME_EXT: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}
_EXT_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
}

#: 对外暴露的媒体根路径（前端 safeImageUrl / 下载按钮都按此前缀放行）
MEDIA_PATH_PREFIX = "/api/v1/media/"


def media_storage() -> LocalStorageBackend:
    return LocalStorageBackend(Path(settings.local_storage_path) / MEDIA_DIR)


def sign_key(key: str) -> str:
    digest = hmac.new(
        settings.app_secret_key.encode("utf-8"),
        f"media|{key}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return digest[:32]


def verify_key(key: str, signature: str) -> bool:
    return bool(signature) and hmac.compare_digest(sign_key(key), signature)


def media_url(key: str) -> str:
    return f"{MEDIA_PATH_PREFIX}{key}?sig={sign_key(key)}"


def ext_for_mime(mime: str) -> str:
    return _MIME_EXT.get((mime or "").lower(), ".bin")


def mime_for_key(key: str) -> str:
    suffix = ("." + key.rsplit(".", 1)[-1].lower()) if "." in key else ""
    return _EXT_MIME.get(suffix, "")


def save_bytes(data: bytes, mime: str) -> str:
    return media_storage().save(data, ext_for_mime(mime))


def read_bytes(key: str) -> bytes:
    with media_storage().open(key) as fh:
        return fh.read()


def read_range(key: str, start: int, length: int) -> bytes:
    path = media_storage().path(key)
    with path.open("rb") as fh:
        fh.seek(max(0, start))
        return fh.read(max(0, length))


def size_of(key: str) -> int:
    return media_storage().path(key).stat().st_size


def delete(key: str) -> None:
    media_storage().delete(key)


__all__ = [
    "MEDIA_DIR",
    "MEDIA_PATH_PREFIX",
    "delete",
    "ext_for_mime",
    "media_storage",
    "media_url",
    "mime_for_key",
    "read_bytes",
    "read_range",
    "save_bytes",
    "sign_key",
    "size_of",
    "verify_key",
]
