"""演示主题（DeckTheme）：颜色、中文字体回退链、字号阶梯、页边距与网格。

设计口径：
- **配色受控**：每一套主题的主色都能在白色背景上达到 WCAG AA（≥4.5:1），
  渲染器不再各页各调颜色，避免「一页一个色」的低质观感；
- **字体显式声明**：中文用 ``a:ea`` 写入具体字体名（如「思源黑体」「微软雅黑」
  「PingFang SC」），拉丁文字另给 ``a:latin``，缺字体时由 Office 按字符集回退，
  不会把中文渲染成方框；
- **版式有标尺**：字号、页边距、栏间距、栅格统一由主题给出，渲染器与质检共用
  同一套数字，质检的「字号过小 / 越界」才有确定口径。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")

#: 中文无衬线回退链（Windows / macOS / 开源字体都覆盖）
SANS_CN: tuple[str, ...] = ("思源黑体", "微软雅黑", "PingFang SC", "Noto Sans CJK SC")
#: 中文衬线回退链（党政、学术场景的标题更稳重）
SERIF_CN: tuple[str, ...] = ("思源宋体", "宋体", "Songti SC", "Noto Serif CJK SC")
#: 拉丁字体（只放拉丁字形，避免覆盖中文）
LATIN_SANS = "Arial"
LATIN_SERIF = "Georgia"


@dataclass(frozen=True)
class DeckTheme:
    """一套演示主题的完整设计令牌。"""

    name: str
    label: str
    primary: str
    secondary: str
    accent: str
    neutral_dark: str
    neutral_mid: str
    neutral_light: str
    background: str
    font_title: tuple[str, ...] = SANS_CN
    font_body: tuple[str, ...] = SANS_CN
    latin_title: str = LATIN_SANS
    latin_body: str = LATIN_SANS
    # 字号阶梯（pt）
    cover_title: int = 40
    section_title: int = 34
    title: int = 30
    subtitle: int = 20
    body: int = 18
    bullet: int = 18
    small: int = 14
    caption: int = 12
    kpi: int = 40
    quote: int = 28
    table: int = 14
    footer: int = 10
    # 版面标尺（英寸）
    margin: float = 0.6
    gutter: float = 0.3
    grid_cols: int = 12
    grid_rows: int = 6
    type_scale: dict[str, int] = field(default_factory=dict)

    def scale(self, key: str) -> int:
        """字号查询：先查显式阶梯，再回落到正文字号。"""
        return int(self.type_scale.get(key, self.body))

    @property
    def title_stack(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.font_title, *SANS_CN)))

    @property
    def body_stack(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.font_body, *SANS_CN)))


def _scale(**kwargs: int) -> dict[str, int]:
    return dict(kwargs)


#: 8 套主题。所有 primary/secondary 均在白底上 ≥4.5:1（见 tests/unit/test_deck_theme.py）。
_THEMES: dict[str, DeckTheme] = {
    "gov_red": DeckTheme(
        name="gov_red",
        label="党政红",
        primary="#9E1B22",
        secondary="#6E1418",
        accent="#9A6B06",
        neutral_dark="#1F1F1F",
        neutral_mid="#595959",
        neutral_light="#F3F2EF",
        background="#FFFFFF",
        font_title=SERIF_CN,
        font_body=SANS_CN,
        latin_title=LATIN_SERIF,
        latin_body=LATIN_SANS,
        type_scale=_scale(cover=42, section=34, title=30, body=18, table=13),
    ),
    "tech_blue": DeckTheme(
        name="tech_blue",
        label="科技蓝",
        primary="#0F4C81",
        secondary="#2F6DB5",
        accent="#0E7C7B",
        neutral_dark="#16202C",
        neutral_mid="#5A6B7B",
        neutral_light="#F1F5FA",
        background="#FFFFFF",
        type_scale=_scale(cover=42, section=34, title=30, body=18, table=14),
    ),
    "agri_green": DeckTheme(
        name="agri_green",
        label="农科绿",
        primary="#1F6B3B",
        secondary="#25784B",
        accent="#8A6D1D",
        neutral_dark="#1B2A21",
        neutral_mid="#556B5D",
        neutral_light="#F2F7F2",
        background="#FFFFFF",
        type_scale=_scale(cover=42, section=34, title=30, body=18, table=14),
    ),
    "business_navy": DeckTheme(
        name="business_navy",
        label="商务藏青",
        primary="#12233F",
        secondary="#1F3A5F",
        accent="#8A6A2F",
        neutral_dark="#0F1723",
        neutral_mid="#5B6572",
        neutral_light="#F3F4F6",
        background="#FFFFFF",
        type_scale=_scale(cover=40, section=32, title=28, body=18, table=13),
    ),
    "minimal_mono": DeckTheme(
        name="minimal_mono",
        label="极简灰",
        primary="#262626",
        secondary="#4A4A4A",
        accent="#6B6B6B",
        neutral_dark="#1A1A1A",
        neutral_mid="#666666",
        neutral_light="#F5F5F5",
        background="#FFFFFF",
        type_scale=_scale(cover=38, section=32, title=28, body=18, table=13),
    ),
    "warm_orange": DeckTheme(
        name="warm_orange",
        label="暖橙",
        primary="#A63A0B",
        secondary="#C2410C",
        accent="#0F766E",
        neutral_dark="#1C1917",
        neutral_mid="#6B6259",
        neutral_light="#FDF3E8",
        background="#FFFFFF",
        type_scale=_scale(cover=42, section=34, title=30, body=18, table=14),
    ),
    "academic": DeckTheme(
        name="academic",
        label="学术靛",
        primary="#3A3A6E",
        secondary="#56568F",
        accent="#7A5C12",
        neutral_dark="#1E1E2A",
        neutral_mid="#5A5A6E",
        neutral_light="#F4F4F8",
        background="#FFFFFF",
        font_title=SERIF_CN,
        font_body=SANS_CN,
        latin_title=LATIN_SERIF,
        type_scale=_scale(cover=40, section=32, title=28, body=18, table=13),
    ),
    "forest_green": DeckTheme(
        name="forest_green",
        label="森林绿",
        primary="#14532D",
        secondary="#166534",
        accent="#8A5A0B",
        neutral_dark="#0F2A1B",
        neutral_mid="#4B5D52",
        neutral_light="#F1F6F2",
        background="#FFFFFF",
        type_scale=_scale(cover=42, section=34, title=30, body=18, table=14),
    ),
}

THEME_NAMES: tuple[str, ...] = tuple(_THEMES)
DEFAULT_THEME = "tech_blue"

#: 主题别名：模型常写「政务红」「科技蓝」这类中文名
_ALIASES: dict[str, str] = {
    "gov": "gov_red",
    "government": "gov_red",
    "gov_red": "gov_red",
    "政务": "gov_red",
    "党政": "gov_red",
    "党建": "gov_red",
    "tech": "tech_blue",
    "blue": "tech_blue",
    "科技": "tech_blue",
    "商务": "business_navy",
    "navy": "business_navy",
    "agri": "agri_green",
    "green": "agri_green",
    "农业": "agri_green",
    "农科": "agri_green",
    "minimal": "minimal_mono",
    "mono": "minimal_mono",
    "极简": "minimal_mono",
    "orange": "warm_orange",
    "暖橙": "warm_orange",
    "academic": "academic",
    "学术": "academic",
    "forest": "forest_green",
    "森林": "forest_green",
}


def theme_names() -> tuple[str, ...]:
    return THEME_NAMES


def get_theme(name: str | DeckTheme | None) -> DeckTheme:
    """按名取主题；未知主题回落到 tech_blue（生成链路不允许因配色失败）。"""
    if isinstance(name, DeckTheme):
        return name
    key = (name or "").strip().lower().replace("-", "_").replace(" ", "_")
    if key in _THEMES:
        return _THEMES[key]
    alias = _ALIASES.get(key)
    if alias:
        return _THEMES[alias]
    return _THEMES[DEFAULT_THEME]


def _norm(color: str) -> str:
    match = _HEX_RE.match((color or "").strip())
    if not match:
        raise ValueError(f"颜色必须是 #RRGGBB：{color!r}")
    return match.group(1).upper()


def rgb(color: str) -> tuple[int, int, int]:
    value = _norm(color)
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _channel_luminance(value: int) -> float:
    channel = value / 255.0
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def relative_luminance(color: str) -> float:
    """WCAG 2.x 相对亮度。"""
    r, g, b = rgb(color)
    return (
        0.2126 * _channel_luminance(r)
        + 0.7152 * _channel_luminance(g)
        + 0.0722 * _channel_luminance(b)
    )


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG 对比度（1.0–21.0）。"""
    a = relative_luminance(foreground)
    b = relative_luminance(background)
    lighter, darker = max(a, b), min(a, b)
    return round((lighter + 0.05) / (darker + 0.05), 2)


def _mix(color: str, target: str, ratio: float) -> str:
    r1, g1, b1 = rgb(color)
    r2, g2, b2 = rgb(target)
    mixed = (round(c1 + (c2 - c1) * ratio) for c1, c2 in ((r1, r2), (g1, g2), (b1, b2)))
    return "#" + "".join(f"{value:02X}" for value in mixed)


def ensure_contrast(
    foreground: str,
    background: str,
    minimum: float = 4.5,
    *,
    light_fallback: str = "#FFFFFF",
    dark_fallback: str = "#111111",
) -> str:
    """把前景色调整到与背景至少 ``minimum`` 对比度（确定性，不做随机试探）。

    先按「往黑 / 往白」哪个方向更可能提升对比度来选方向，再按 10% 步长混色；
    仍不达标时回落到黑或白，保证可读性优先于配色一致性。
    """
    if contrast_ratio(foreground, background) >= minimum:
        return foreground if foreground.startswith("#") else f"#{_norm(foreground)}"
    bg_lum = relative_luminance(background)
    target = "#000000" if bg_lum > 0.5 else "#FFFFFF"
    best = foreground
    best_ratio = contrast_ratio(foreground, background)
    for step in range(1, 11):
        candidate = _mix(foreground, target, step / 10)
        ratio = contrast_ratio(candidate, background)
        if ratio > best_ratio:
            best, best_ratio = candidate, ratio
        if ratio >= minimum:
            return candidate
    fallback = dark_fallback if bg_lum > 0.5 else light_fallback
    if contrast_ratio(fallback, background) >= best_ratio:
        return fallback
    return best


__all__ = [
    "DEFAULT_THEME",
    "DeckTheme",
    "LATIN_SANS",
    "LATIN_SERIF",
    "SANS_CN",
    "SERIF_CN",
    "THEME_NAMES",
    "contrast_ratio",
    "ensure_contrast",
    "get_theme",
    "relative_luminance",
    "rgb",
    "theme_names",
]
