"""增长域 HTTP 入口（v3.26 D-1.4）：海报出图与取图。

- ``POST /growth/posters``：登录态出图（与 ``image.generate`` 工具同一条实现）；
- ``GET /growth/posters/{key}``：取图。图片由 ``<img>`` 直接加载、带不了
  Authorization 头，因此这里用「随机存储键 + HMAC 签名」的 capability URL，
  不依赖登录态；只读 ``posters/`` 子目录，不会暴露知识库/附件等其它文件。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.deps import YardContext, get_db_session, get_yard_context
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.growth.poster import (
    generate_poster,
    poster_storage,
    verify_key,
)
from app.modules.llm.images import ALLOWED_SIZES, MAX_PROMPT_LEN

router = APIRouter(prefix="/growth", tags=["growth"])

_EXT_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}


class PosterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=4, max_length=MAX_PROMPT_LEN)
    size: str = Field(default="1024x1024")
    n: int = Field(default=1, ge=1, le=4)
    title: str = Field(default="", max_length=40)
    subtitle: str = Field(default="", max_length=120)
    footer: str = Field(default="", max_length=120)
    theme: Literal["harvest", "orchard", "plain"] = "harvest"
    negative_prompt: str | None = Field(default=None, max_length=400)

    def checked_size(self) -> str:
        if self.size not in ALLOWED_SIZES:
            raise AppError(
                "INVALID_REQUEST",
                f"不支持的出图尺寸：{self.size}。可选：{'、'.join(ALLOWED_SIZES)}。",
                http_status=400,
            )
        return self.size


@router.post("/posters", response_model=None)
async def create_poster(
    payload: PosterRequest,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    result = await generate_poster(
        db,
        yard_id=ctx.yard_id,
        prompt=payload.prompt,
        size=payload.checked_size(),
        n=payload.n,
        title=payload.title,
        subtitle=payload.subtitle,
        footer=payload.footer,
        theme=payload.theme,
        negative_prompt=payload.negative_prompt,
    )
    return ok(result)


@router.get("/posters/{key:path}", response_model=None)
def read_poster(key: str, sig: str = ""):
    """按签名取图；签名不符/文件缺失一律 404（不泄露键是否存在）。"""
    missing = AppError("POSTER_NOT_FOUND", "海报不存在或链接已失效。", http_status=404)
    if not verify_key(key, sig):
        raise missing
    suffix = ("." + key.rsplit(".", 1)[-1].lower()) if "." in key else ""
    mime = _EXT_MIME.get(suffix)
    if mime is None:
        raise missing
    try:
        with poster_storage().open(key) as fh:
            data = fh.read()
    except (FileNotFoundError, ValueError, OSError) as e:
        raise missing from e
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "private, max-age=86400"},
    )


__all__ = ["router"]
