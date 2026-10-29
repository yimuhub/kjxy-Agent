"""海报生成：自然语言 → 图像供应商出图；无可用供应商时降级本地模板（v3.26 D-1.4）。

三段职责：

1. ``build_template_svg``：纯本地 SVG 模板（标题/副标题/页脚/二维码位 + 三套配色），
   不需要字体文件与图像库，任何环境都能出图；
2. ``generate_poster``：沿 3.25 绑定链取图像供应商出图，失败或缺配置**降级模板**
   并在 ``notice`` 里如实说明（不假装是 AI 出图）；
3. ``image.generate`` 工具：智能体/技能调用入口，产物落到 ``posters/`` 独立子目录，
   以带签名的方式对外提供（对话里可以直接 ``![](url)`` 显示）。

存储隔离：海报写在 ``local_storage_path/posters`` 下，与知识库/附件/首图的
存储键不共用目录，取图端点只读这一层，不会顺带暴露其它文件。
"""

from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.knowledge.storage import LocalStorageBackend
from app.modules.llm.images import (
    ALLOWED_SIZES,
    DEFAULT_SIZE,
    MAX_PROMPT_LEN,
    ImageAsset,
    ImageRequest,
    normalize_count,
    normalize_size,
    resolve_image_provider,
)

log = get_logger(__name__)

TOOL_CODE = "image.generate"
POSTER_DIR = "posters"
DEFAULT_THEME = "harvest"
#: 工具超时：供应商轮询上限 150s，留出落盘余量
TOOL_TIMEOUT_SECONDS = 200

_MIME_EXT: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
}
_EXT_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}

#: 三套配色：暖收成 / 果园绿 / 素雅灰
THEMES: dict[str, dict[str, str]] = {
    "harvest": {
        "bg1": "#FDF3E3",
        "bg2": "#F3D3A0",
        "ink": "#3E2A14",
        "accent": "#C2571A",
        "soft": "#8C6A3F",
    },
    "orchard": {
        "bg1": "#EDF7F0",
        "bg2": "#BFE0C9",
        "ink": "#123B22",
        "accent": "#2E7D32",
        "soft": "#4C7A57",
    },
    "plain": {
        "bg1": "#F6F7F9",
        "bg2": "#DFE3EA",
        "ink": "#1F2329",
        "accent": "#3A6EA5",
        "soft": "#6B7280",
    },
}


def poster_storage() -> LocalStorageBackend:
    return LocalStorageBackend(Path(settings.local_storage_path) / POSTER_DIR)


def sign_key(key: str) -> str:
    """对存储键做 HMAC 签名：取图无需登录态，但必须持签名（capability URL）。"""
    digest = hmac.new(
        settings.app_secret_key.encode("utf-8"), key.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return digest[:32]


def verify_key(key: str, signature: str) -> bool:
    return bool(signature) and hmac.compare_digest(sign_key(key), signature)


def poster_url(key: str) -> str:
    return f"/api/v1/growth/posters/{key}?sig={sign_key(key)}"


def _split_size(size: str) -> tuple[int, int]:
    width, _, height = normalize_size(size).partition("x")
    return int(width), int(height)


def _wrap(text: str, per_line: int, max_lines: int) -> list[str]:
    clean = " ".join(str(text or "").split())
    if not clean:
        return []
    lines: list[str] = []
    for start in range(0, len(clean), per_line):
        lines.append(clean[start : start + per_line])
        if len(lines) >= max_lines:
            break
    return lines


def build_template_svg(
    *,
    title: str,
    subtitle: str = "",
    footer: str = "",
    size: str = DEFAULT_SIZE,
    theme: str = DEFAULT_THEME,
) -> bytes:
    """本地模板海报（SVG）。只做排版，不做图像生成。"""
    palette = THEMES.get(theme) or THEMES[DEFAULT_THEME]
    width, height = _split_size(size)
    title_size = max(28, int(width * 0.085))
    subtitle_size = max(18, int(width * 0.036))
    footer_size = max(14, int(width * 0.026))
    margin = int(width * 0.09)
    usable = width - margin * 2
    per_line = max(4, int(usable / (title_size * 1.02)))
    title_lines = _wrap(title, per_line, 3)
    subtitle_lines = _wrap(subtitle, max(8, int(usable / (subtitle_size * 1.05))), 4)
    qr = int(width * 0.17)
    qr_x = width - margin - qr
    qr_y = height - margin - qr

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        "<defs>",
        '<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">',
        f'<stop offset="0%" stop-color="{palette["bg1"]}"/>',
        f'<stop offset="100%" stop-color="{palette["bg2"]}"/>',
        "</linearGradient>",
        "</defs>",
        f'<rect width="{width}" height="{height}" fill="url(#bg)"/>',
        (
            f'<circle cx="{int(width * 0.88)}" cy="{int(height * 0.12)}" '
            f'r="{int(width * 0.16)}" fill="{palette["accent"]}" opacity="0.12"/>'
        ),
        (
            f'<circle cx="{int(width * 0.08)}" cy="{int(height * 0.9)}" '
            f'r="{int(width * 0.22)}" fill="{palette["soft"]}" opacity="0.14"/>'
        ),
        (
            f'<rect x="{margin}" y="{int(height * 0.16)}" width="{int(width * 0.12)}" '
            f'height="{max(6, int(height * 0.008))}" fill="{palette["accent"]}"/>'
        ),
    ]
    y = int(height * 0.26)
    for line in title_lines:
        parts.append(
            f'<text x="{margin}" y="{y}" font-family="PingFang SC, Hiragino Sans GB, '
            "Microsoft YaHei, Noto Sans CJK SC, sans-serif\" "
            f'font-size="{title_size}" font-weight="700" fill="{palette["ink"]}">'
            f"{escape(line)}</text>"
        )
        y += int(title_size * 1.25)
    y += int(title_size * 0.3)
    for line in subtitle_lines:
        parts.append(
            f'<text x="{margin}" y="{y}" font-family="PingFang SC, Hiragino Sans GB, '
            "Microsoft YaHei, Noto Sans CJK SC, sans-serif\" "
            f'font-size="{subtitle_size}" fill="{palette["soft"]}">{escape(line)}</text>'
        )
        y += int(subtitle_size * 1.5)
    parts.append(
        f'<rect x="{qr_x}" y="{qr_y}" width="{qr}" height="{qr}" fill="#FFFFFF" '
        f'fill-opacity="0.85" stroke="{palette["soft"]}" stroke-width="2" '
        f'stroke-dasharray="8 6" rx="{int(qr * 0.06)}"/>'
    )
    parts.append(
        f'<text x="{qr_x + qr // 2}" y="{qr_y + qr // 2}" text-anchor="middle" '
        f'font-family="PingFang SC, Microsoft YaHei, sans-serif" font-size="{footer_size}" '
        f'fill="{palette["soft"]}">二维码位</text>'
    )
    if footer:
        parts.append(
            f'<text x="{margin}" y="{height - margin}" font-family="PingFang SC, '
            "Microsoft YaHei, Noto Sans CJK SC, sans-serif\" "
            f'font-size="{footer_size}" fill="{palette["soft"]}">{escape(footer)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts).encode("utf-8")


def _derive_title(prompt: str) -> str:
    """没有显式标题时，用需求描述的前 18 个字兜底（不臆造品类/品牌）。"""
    clean = " ".join(str(prompt or "").split())
    return clean[:18]


def _save_assets(assets: list[ImageAsset]) -> list[dict[str, Any]]:
    storage = poster_storage()
    saved: list[dict[str, Any]] = []
    for asset in assets:
        ext = _MIME_EXT.get(asset.mime, ".bin")
        key = storage.save(asset.data, ext)
        saved.append(
            {"key": key, "url": poster_url(key), "mime": asset.mime, "bytes": len(asset.data)}
        )
    return saved


async def generate_poster(
    db: Session,
    *,
    yard_id: str,
    prompt: str,
    size: str | None = None,
    n: int = 1,
    title: str = "",
    subtitle: str = "",
    footer: str = "",
    theme: str = DEFAULT_THEME,
    negative_prompt: str | None = None,
    agent_code: str | None = None,
) -> dict[str, Any]:
    """出海报：优先图像供应商，失败/未配置则降级模板，并在 notice 里说明。"""
    text = " ".join(str(prompt or "").split())
    if len(text) < 4:
        raise AppError("INVALID_REQUEST", "海报需求描述过短（至少 4 个字）。", http_status=400)
    text = text[:MAX_PROMPT_LEN]
    clean_title = " ".join(str(title or "").split())[:40] or _derive_title(text)
    clean_size = normalize_size(size)
    started = time.perf_counter()

    provider_label: str | None = None
    model = ""
    fallback = True
    notice = ""
    assets: list[ImageAsset] = []
    resolved = resolve_image_provider(db, yard_id, agent_code=agent_code)
    if resolved is None:
        notice = (
            "尚未配置图像生成供应商，已用本地模板渲染。"
            "在「模型中心 → 供应商」新增图像生成供应商"
            "（图像生成·OpenAI 兼容 / 阿里云百炼）后即可出 AI 海报。"
        )
    else:
        provider, _level = resolved
        try:
            batch = await provider.generate(
                ImageRequest(
                    prompt=text,
                    size=clean_size,
                    n=normalize_count(n),
                    negative_prompt=(negative_prompt or "").strip() or None,
                )
            )
            assets = list(batch.assets)
            provider_label = batch.provider_label
            model = batch.model
            fallback = False
            notice = ""
        except AppError as e:
            log.warning("图像生成失败，降级模板：%s", e.message)
            provider_label = getattr(provider, "provider_label", None)
            notice = f"图像供应商本次不可用（{e.message}），已用本地模板渲染，可稍后重试。"

    if not assets:
        assets = [
            ImageAsset(
                data=build_template_svg(
                    title=clean_title,
                    subtitle=subtitle,
                    footer=footer,
                    size=clean_size,
                    theme=theme,
                ),
                mime="image/svg+xml",
            )
        ]
        fallback = True
    saved = _save_assets(assets)
    return {
        "ok": True,
        "fallback": fallback,
        "notice": notice,
        "provider": provider_label,
        "model": model,
        "title": clean_title,
        "size": clean_size,
        "assets": saved,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }


INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "minLength": 4,
            "maxLength": MAX_PROMPT_LEN,
            "description": "给图像模型的结构化提示词（主体/场景/风格/构图/光线/文字区留白/尺寸比）",
        },
        "size": {"type": "string", "enum": list(ALLOWED_SIZES), "description": "出图尺寸"},
        "n": {"type": "integer", "minimum": 1, "maximum": 4, "description": "出图张数"},
        "title": {"type": "string", "maxLength": 40, "description": "模板降级时的大标题"},
        "subtitle": {"type": "string", "maxLength": 120, "description": "模板降级时的副标题"},
        "footer": {
            "type": "string",
            "maxLength": 120,
            "description": "模板降级时的页脚（小院名/电话）",
        },
        "theme": {
            "type": "string",
            "enum": list(THEMES),
            "description": "模板配色：harvest 暖收成 / orchard 果园绿 / plain 素雅灰",
        },
        "negative_prompt": {"type": "string", "maxLength": 400, "description": "不希望出现的元素"},
    },
    "required": ["prompt"],
    "additionalProperties": False,
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "fallback": {"type": "boolean", "description": "true=未用上图像模型，产物是本地模板"},
        "notice": {"type": "string"},
        "provider": {"type": ["string", "null"]},
        "model": {"type": "string"},
        "title": {"type": "string"},
        "size": {"type": "string"},
        "assets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "url": {"type": "string"},
                    "mime": {"type": "string"},
                    "bytes": {"type": "integer"},
                },
            },
        },
        "latency_ms": {"type": "integer"},
    },
    "required": ["ok", "fallback", "notice", "assets"],
}


async def generate_image_tool_func(params: dict, ctx: Any = None) -> dict:
    """工具入口：图像供应商解析要查库，因此这里自开一个只读会话。"""
    if ctx is None or not getattr(ctx, "yard_id", None):
        raise AppError("YARD_REQUIRED", "图像生成需要小院上下文。", http_status=400)
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        return await generate_poster(
            db,
            yard_id=ctx.yard_id,
            prompt=str(params.get("prompt") or ""),
            size=params.get("size"),
            n=int(params.get("n") or 1),
            title=str(params.get("title") or ""),
            subtitle=str(params.get("subtitle") or ""),
            footer=str(params.get("footer") or ""),
            theme=str(params.get("theme") or DEFAULT_THEME),
            negative_prompt=params.get("negative_prompt"),
        )
    finally:
        db.close()


def tool_meta() -> dict[str, Any]:
    """工具目录元数据（写入 tool_definitions，供「可用工具」展示）。"""
    return {
        "code": TOOL_CODE,
        "version": "1.0.0",
        "name": "图像生成（海报出图）",
        "description": (
            "按结构化提示词调用图像生成供应商出图，产物落到海报目录并返回可访问 URL；"
            "未配置图像供应商或供应商失败时降级本地 SVG 模板，并在 notice 里如实说明。"
        ),
        "input_schema_json": INPUT_SCHEMA,
        "output_schema_json": OUTPUT_SCHEMA,
        "required_permissions_json": [],
        "risk_level": "write_reversible",
        "side_effect": False,
        "timeout_seconds": TOOL_TIMEOUT_SECONDS,
        "enabled": True,
    }


def register_image_tool() -> Any:
    """注册 code=image.generate（写本地文件，不对外部世界产生承诺）。"""
    try:
        from app.modules.tools.registry import ToolDef, tool_registry
    except Exception as e:  # pragma: no cover - 注册表缺失时不影响 import
        log.warning("工具注册表不可用，image.generate 未注册: %s", e)
        return None
    meta = tool_meta()
    tool_def = ToolDef(
        code=TOOL_CODE,
        version=str(meta["version"]),
        name=str(meta["name"]),
        description=str(meta["description"]),
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        required_permissions=[],
        risk_level="write_reversible",
        side_effect=False,
        timeout_seconds=TOOL_TIMEOUT_SECONDS,
        func=generate_image_tool_func,
    )
    try:
        tool_registry.register(tool_def)
    except Exception as e:  # pragma: no cover
        log.warning("image.generate 注册失败: %s", e)
        return None
    return tool_def


__all__ = [
    "DEFAULT_THEME",
    "THEMES",
    "TOOL_CODE",
    "build_template_svg",
    "generate_poster",
    "poster_storage",
    "poster_url",
    "register_image_tool",
    "sign_key",
    "tool_meta",
    "verify_key",
]
