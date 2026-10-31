"""演示主题单测：8 套齐全、对比度达标、未知主题回落。"""

from __future__ import annotations

import pytest
from app.modules.docs.deck_theme import (
    DEFAULT_THEME,
    THEME_NAMES,
    DeckTheme,
    contrast_ratio,
    ensure_contrast,
    get_theme,
    relative_luminance,
    theme_names,
)

EXPECTED = (
    "gov_red",
    "tech_blue",
    "agri_green",
    "business_navy",
    "minimal_mono",
    "warm_orange",
    "academic",
    "forest_green",
)


def test_eight_themes_registered() -> None:
    assert set(THEME_NAMES) == set(EXPECTED)
    assert theme_names() == THEME_NAMES
    for name in EXPECTED:
        theme = get_theme(name)
        assert isinstance(theme, DeckTheme)
        assert theme.name == name
        assert theme.label


@pytest.mark.parametrize("name", EXPECTED)
def test_theme_colors_and_type_scale(name: str) -> None:
    theme = get_theme(name)
    for color in (
        theme.primary,
        theme.secondary,
        theme.accent,
        theme.neutral_dark,
        theme.neutral_mid,
    ):
        assert color.startswith("#") and len(color) == 7
        assert contrast_ratio(color, theme.background) >= 4.5, (name, color)
    # 封面用主色铺底、白字，必须可读
    assert contrast_ratio("#FFFFFF", theme.primary) >= 4.5
    assert contrast_ratio(theme.neutral_light, theme.background) < 1.5
    # 字号阶梯：封面 > 章节 > 页标题 > 正文 > 注释
    assert theme.cover_title > theme.section_title >= theme.title > theme.body > theme.footer
    assert theme.margin == pytest.approx(0.6)
    assert theme.grid_cols >= 6 and theme.grid_rows >= 4


@pytest.mark.parametrize("name", EXPECTED)
def test_chinese_font_fallback_chain(name: str) -> None:
    theme = get_theme(name)
    assert len(theme.body_stack) >= 3
    assert "微软雅黑" in theme.body_stack
    assert "PingFang SC" in theme.body_stack
    assert theme.latin_body.isascii()
    assert theme.font_title[0] != theme.latin_title


def test_unknown_theme_falls_back() -> None:
    fallback = get_theme(DEFAULT_THEME)
    assert get_theme("不存在的主题") is fallback
    assert get_theme("") is fallback
    assert get_theme(None) is fallback
    assert get_theme("Tech-Blue") is fallback
    assert get_theme("党政") is get_theme("gov_red")
    assert get_theme("农业") is get_theme("agri_green")


def test_get_theme_accepts_instance() -> None:
    theme = get_theme("academic")
    assert get_theme(theme) is theme


def test_contrast_ratio_extremes() -> None:
    assert contrast_ratio("#000000", "#FFFFFF") == 21.0
    assert contrast_ratio("#FFFFFF", "#FFFFFF") == 1.0
    assert relative_luminance("#000000") == pytest.approx(0.0)
    assert relative_luminance("#FFFFFF") == pytest.approx(1.0, abs=1e-6)


def test_ensure_contrast_repairs_low_contrast() -> None:
    weak = "#EEEEEE"
    assert contrast_ratio(weak, "#FFFFFF") < 4.5
    fixed = ensure_contrast(weak, "#FFFFFF")
    assert contrast_ratio(fixed, "#FFFFFF") >= 4.5


def test_ensure_contrast_keeps_good_colors() -> None:
    assert ensure_contrast("#0F4C81", "#FFFFFF") == "#0F4C81"
    # 深色底上自动往白走
    fixed = ensure_contrast("#222222", "#111111")
    assert contrast_ratio(fixed, "#111111") >= 4.5


def test_invalid_color_rejected() -> None:
    with pytest.raises(ValueError):
        contrast_ratio("red", "#FFFFFF")
