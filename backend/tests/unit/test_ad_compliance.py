"""广告合规体检（growth/compliance.py）单元测试。

覆盖：词库规模与结构、四类硬违规命中、需举证类分级、举证例外降级、
长词优先、误杀防护、遮罩改写、strict 口径、空输入与位置正确性。
"""

from __future__ import annotations

import pytest
from app.modules.growth.compliance import (
    check_copy,
    load_glossary,
    scan_text,
    term_count,
)


def test_glossary_scale_and_shape() -> None:
    """词库达到 200+ 条，且每组字段齐全、组内无重复词。"""
    assert term_count() >= 200
    for group in load_glossary().groups:
        assert group.label and group.basis and group.suggestion
        assert group.severity in {"error", "warn"}
        assert group.terms, f"{group.key} 词条为空"
        assert len(set(group.terms)) == len(group.terms), f"{group.key} 存在重复词"


@pytest.mark.parametrize(
    ("text", "group", "severity"),
    [
        ("这是全村长势最好的脐橙", "absolute_superlative", "error"),
        ("本店销量第一，全国领先", "absolute_rank", "error"),
        ("国家级示范基地出品", "absolute_official", "error"),
        ("常吃可以降血压、降血糖", "food_medical", "error"),
        ("100%纯天然，绝对安全", "food_absolute", "error"),
        ("包治百病的神效水果", "exaggeration", "error"),
    ],
)
def test_hard_violations(text: str, group: str, severity: str) -> None:
    """硬违规类命中且分级为 error。"""
    hits = scan_text(text)
    assert any(h.group == group and h.severity == severity for h in hits), hits


@pytest.mark.parametrize(
    ("text", "group"),
    [
        ("本品通过有机认证", "certification"),
        ("特级果，单果 80mm 以上", "grade_claim"),
        ("买贵退差，终身保修", "price_promise"),
        ("永久保鲜不腐烂", "time_claim"),
        ("调理肠胃、增强体质", "health_claim_borderline"),
    ],
)
def test_needs_evidence_groups_are_warn(text: str, group: str) -> None:
    """需举证类默认只提示（warn），不直接阻断。"""
    hits = [h for h in scan_text(text) if h.group == group]
    assert hits and all(h.severity == "warn" and not h.downgraded for h in hits)


def test_exception_downgrades_error_to_warn() -> None:
    """同句出现举证标记时，硬违规降级为提示（依据执法指南的有依据例外）。"""
    text = "经第三方报告实测，本批次甜度最佳，糖度 13.2°"
    hits = [h for h in scan_text(text) if h.term == "最佳"]
    assert hits and hits[0].severity == "warn" and hits[0].downgraded


def test_no_downgrade_without_evidence() -> None:
    """没有举证标记时不得降级。"""
    hits = [h for h in scan_text("本批次甜度最佳") if h.term == "最佳"]
    assert hits and hits[0].severity == "error" and not hits[0].downgraded


def test_exception_only_applies_within_same_sentence() -> None:
    """举证标记在另一句时，不构成降级依据。"""
    text = "经检测报告实测，糖度达标。本批次甜度最佳。"
    hits = [h for h in scan_text(text) if h.term == "最佳"]
    assert hits and hits[0].severity == "error"


def test_longest_term_wins() -> None:
    """重叠命中保留最长的词，避免同一处报两遍。"""
    hits = [h for h in scan_text("最优质果礼盒") if h.start < 5]
    assert len(hits) == 1
    assert hits[0].term == "最优质果"


@pytest.mark.parametrize(
    "text",
    [
        "最近三天温差较大，注意保温",
        "最后一批货已入库",
        "今日最高气温 32℃，注意通风",
        "建议在上午 9 点前完成采收",
    ],
)
def test_no_false_positive_on_daily_sentences(text: str) -> None:
    """日常农业表述不应被误判（最高气温/最近/最后等非广告语境）。"""
    assert scan_text(text) == []


def test_rewritten_masks_only_hard_violations() -> None:
    """遮罩只替换 error 命中；需举证词保留原样。"""
    result = check_copy("全国第一的有机脐橙，最好吃", strict=False)
    assert "〔需改写〕" in result["rewritten"]
    assert "有机" in result["rewritten"]  # warn 不遮罩
    assert result["ok"] is False


def test_warn_only_blocks_in_strict_mode() -> None:
    """只有需举证类时：strict=True 不通过，strict=False 通过。"""
    text = "本品为有机种植"
    assert check_copy(text, strict=True)["ok"] is False
    assert check_copy(text, strict=False)["ok"] is True


def test_clean_copy_passes() -> None:
    """无命中词时通过，且汇总文案明确。"""
    result = check_copy("赣南脐橙，单果 200–250g，0–4℃ 冷藏可存 7 天。")
    assert result["ok"] is True
    assert result["hit_count"] == 0
    assert result["summary_text"] == "未发现命中词。"


def test_empty_text_is_rejected() -> None:
    """空文案返回不通过而不是抛错。"""
    result = check_copy("   ")
    assert result["ok"] is False
    assert result["hit_count"] == 0


def test_hit_positions_point_at_term() -> None:
    """命中位置必须能切出原词，供前端高亮。"""
    text = "这款苹果是全村最好的，销量第一。"
    for hit in scan_text(text):
        assert text[hit.start : hit.end] == hit.term


def test_ascii_terms_are_case_insensitive() -> None:
    """英文词条（No.1/TOP1）大小写不敏感。"""
    assert scan_text("我们是 no.1 的合作社")
    assert scan_text("我们是 TOP1 的合作社")


def test_counts_match_hits() -> None:
    """汇总计数与明细一致。"""
    result = check_copy("全国第一，有机认证，包治百病", strict=False)
    assert result["hit_count"] == len(result["hits"])
    assert result["error_count"] + result["warn_count"] == result["hit_count"]
    assert result["error_count"] >= 2
