"""媒体取流与状态查询（v3.73）。

- ``GET /media/{key}``：capability URL（随机键 + HMAC 签名），``<img>/<video>``
  可直接加载；支持 Range（206），视频可拖动进度条；``download=1`` 带附件名。
- ``GET /media/assets/{id}``：登录态查单个资产状态（视频生成中前端轮询用）。
- ``GET /media/usage``：当前用户的图片/视频当日额度快照。

取流端点不依赖登录态，但只读 ``media/`` 子目录且必须签名，无法遍历其它目录。
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy.orm import Session

from app.core.deps import YardContext, get_db_session, get_yard_context
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.media import service, store

router = APIRouter(prefix="/media", tags=["media"])

_MISSING = AppError("MEDIA_NOT_FOUND", "媒体不存在或链接已失效。", http_status=404)


def _content_disposition(filename: str) -> str:
    return f"attachment; filename*=UTF-8''{quote(filename)}"


@router.get("/usage", response_model=None)
def usage(
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    return ok(service.usage_summary(db, ctx))


@router.get("/assets/{asset_id}", response_model=None)
def asset_status(
    asset_id: str,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    return ok(service.public_asset(db, ctx, asset_id))


@router.get("/{key:path}", response_model=None)
def read_media(
    key: str,
    sig: str = "",
    download: int = Query(default=0),
    range_header: Annotated[str | None, Header(alias="Range")] = None,
):
    if not store.verify_key(key, sig):
        raise _MISSING
    mime = store.mime_for_key(key)
    if not mime:
        raise _MISSING
    try:
        total = store.size_of(key)
    except (FileNotFoundError, ValueError, OSError) as e:
        raise _MISSING from e

    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=86400",
        "X-Content-Type-Options": "nosniff",
    }
    if download:
        headers["Content-Disposition"] = _content_disposition(key.rsplit("/", 1)[-1])

    # Range: bytes=start-end（只处理单段；后缀写法按「最后 N 字节」处理）
    if range_header and range_header.startswith("bytes="):
        spec = range_header[len("bytes=") :].split(",", 1)[0].strip()
        start_text, _, end_text = spec.partition("-")
        try:
            if start_text:
                start = int(start_text)
                end = int(end_text) if end_text else total - 1
            else:
                length = int(end_text or 0)
                start = max(0, total - length)
                end = total - 1
        except ValueError as e:
            raise AppError("INVALID_RANGE", "Range 头格式不正确。", http_status=416) from e
        if start < 0 or start >= total or end < start:
            raise AppError("INVALID_RANGE", "请求的字节范围超出文件大小。", http_status=416)
        end = min(end, total - 1)
        length = end - start + 1
        try:
            chunk = store.read_range(key, start, length)
        except (FileNotFoundError, ValueError, OSError) as e:
            raise _MISSING from e
        headers["Content-Range"] = f"bytes {start}-{end}/{total}"
        headers["Content-Length"] = str(len(chunk))
        return Response(content=chunk, status_code=206, media_type=mime, headers=headers)

    try:
        data = store.read_bytes(key)
    except (FileNotFoundError, ValueError, OSError) as e:
        raise _MISSING from e
    return Response(content=data, media_type=mime, headers=headers)


__all__ = ["router"]
