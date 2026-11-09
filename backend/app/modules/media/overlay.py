"""海报叠字：AI 出底图，中文标题/副标题/页脚由服务端渲染（v3.73）。

为什么不让模型直接画字：中文笔画在扩散模型里经常糊、缺笔、错字，而海报上的
小院名、主题、电话是不能错的。这里把文字层交给 Pillow + 系统中文字体，
底图只负责画面，文字准确率与可控性都由代码保证。

没有可用中文字体时**不伪造**：返回原图并标记 ``overlaid=False``，调用方在
notice 里如实说明「未叠字」。
"""

from __future__ import annotations

import io
import os
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

#: 常见中文字体候选（macOS / Linux / Windows）；可用 MEDIA_FONT_PATH 覆盖
_FONT_CANDIDATES: tuple[str, ...] = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "C:/Windows/Fonts/msyh.ttc",
)

_MAX_TITLE_LINES = 2
_MAX_SUBTITLE_LINES = 2


@lru_cache(maxsize=1)
def font_path() -> str:
    override = (os.environ.get("MEDIA_FONT_PATH") or "").strip()
    if override and Path(override).is_file():
        return override
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return ""


def has_cjk_font() -> bool:
    return bool(font_path())


def _font(size: int) -> FontLike:
    path = font_path()
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


FontLike = ImageFont.FreeTypeFont | ImageFont.ImageFont


def _wrap(text: str, font: FontLike, max_width: int, max_lines: int) -> list[str]:
    """按像素宽度折行（中文逐字、英文尽量按词），超出行数截断加省略号。"""
    clean = " ".join(str(text or "").split())
    if not clean:
        return []
    measure = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines: list[str] = []
    current = ""
    for char in clean:
        probe = current + char
        if measure.textlength(probe, font=font) <= max_width or not current:
            current = probe
            continue
        lines.append(current)
        current = char
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if not lines:
        return []
    # 还没装下的剩余文字 → 末行省略号
    consumed = "".join(lines)
    if len(consumed) < len(clean):
        last = lines[-1]
        while last and measure.textlength(last + "…", font=font) > max_width:
            last = last[:-1]
        lines[-1] = (last or lines[-1][:1]) + "…"
    return lines


def _accent_color(image: Image.Image) -> tuple[int, int, int]:
    """从画面取一个可读的强调色：平均色提亮，用于短横线。"""
    small = image.resize((1, 1)).convert("RGB")
    pixel = small.getpixel((0, 0))
    if not isinstance(pixel, tuple) or len(pixel) < 3:
        return (255, 255, 255)
    r, g, b = (int(pixel[0]), int(pixel[1]), int(pixel[2]))
    boost = 1.35
    return (min(255, int(r * boost)), min(255, int(g * boost)), min(255, int(b * boost)))


def render_poster(
    data: bytes,
    *,
    title: str = "",
    subtitle: str = "",
    footer: str = "",
    mime: str = "image/png",
) -> tuple[bytes, str, bool]:
    """在底图底部叠加标题/副标题/页脚。

    返回 ``(bytes, mime, overlaid)``；``overlaid=False`` 表示没有中文字体，
    返回的是未改动的原图。
    """
    if not (title.strip() or subtitle.strip() or footer.strip()) or not has_cjk_font():
        return data, mime, False
    try:
        base = Image.open(io.BytesIO(data))
        base.load()
    except Exception:  # noqa: BLE001 - 上游可能给了非图片字节，交给调用方按原样处理
        return data, mime, False

    image = base.convert("RGB")
    width, height = image.size
    if width < 320 or height < 320:
        return data, mime, False

    title = " ".join(title.split())[:40]
    subtitle = " ".join(subtitle.split())[:120]
    footer = " ".join(footer.split())[:120]

    margin = max(16, int(width * 0.055))
    band_h = int(height * (0.34 if (subtitle or footer) else 0.26))
    band_h = min(band_h, int(height * 0.45))

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    drawer = ImageDraw.Draw(overlay)
    for offset in range(band_h):
        ratio = offset / max(1, band_h - 1)
        alpha = int(205 * (ratio**1.5))
        drawer.line(
            [(0, height - band_h + offset), (width, height - band_h + offset)],
            fill=(8, 12, 20, alpha),
        )
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(image)

    title_size = max(20, int(width * 0.072))
    subtitle_size = max(14, int(width * 0.036))
    footer_size = max(12, int(width * 0.026))
    title_font = _font(title_size)
    subtitle_font = _font(subtitle_size)
    footer_font = _font(footer_size)
    max_text_width = width - margin * 2

    title_lines = _wrap(title, title_font, max_text_width, _MAX_TITLE_LINES)
    subtitle_lines = _wrap(subtitle, subtitle_font, max_text_width, _MAX_SUBTITLE_LINES)
    footer_lines = _wrap(footer, footer_font, max_text_width, 1)

    title_line_h = int(title_size * 1.32)
    subtitle_line_h = int(subtitle_size * 1.52)
    footer_line_h = int(footer_size * 1.5)
    content_h = (
        len(title_lines) * title_line_h
        + (len(subtitle_lines) * subtitle_line_h if subtitle_lines else 0)
        + (footer_line_h if footer_lines else 0)
        + (int(width * 0.018) if subtitle_lines else 0)
    )

    cursor_y = height - margin - content_h
    accent = _accent_color(base)
    line_w = int(width * 0.09)
    if title_lines:
        bar_top = cursor_y - int(width * 0.02)
        draw.rectangle(
            [(margin, bar_top), (margin + line_w, bar_top + int(width * 0.006))],
            fill=accent,
        )
        cursor_y += int(width * 0.006)
        for line in title_lines:
            draw.text((margin, cursor_y), line, font=title_font, fill=(255, 255, 255))
            cursor_y += title_line_h
    if subtitle_lines:
        cursor_y += int(width * 0.012)
        for line in subtitle_lines:
            draw.text((margin, cursor_y), line, font=subtitle_font, fill=(236, 240, 245))
            cursor_y += subtitle_line_h
    if footer_lines:
        draw.text((margin, cursor_y), footer_lines[0], font=footer_font, fill=(190, 198, 210))

    out = io.BytesIO()
    if (mime or "").lower() in {"image/jpeg", "image/jpg"}:
        image.save(out, format="JPEG", quality=92, optimize=True)
        out_mime = "image/jpeg"
    else:
        image.save(out, format="PNG", optimize=True)
        out_mime = "image/png"
    return out.getvalue(), out_mime, True


__all__ = ["font_path", "has_cjk_font", "render_poster"]
