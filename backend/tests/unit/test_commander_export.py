"""指挥官导出与周报推送单测（v3.56）。

- markdown_to_doc_model：标题层级/列表/编号/长段落切分/空内容兜底；
- _push_weekly_report：本周幂等（重复生成不重复推送）、审计锚点落库。
"""

from types import SimpleNamespace

from app.modules.commander import export as commander_export
from app.modules.commander import reports
from app.modules.yards.repository import create_yard

MD = """# 每周优化报告 2026-W45

周期：11-02 → 11-08

## 一、测评结论

- 人格判定：稳健型；综合分 82。
1. 建议一
2. 建议二

这是普通段落，说明本周运行情况。
"""


def test_markdown_to_doc_model_structure():
    model = commander_export.markdown_to_doc_model(
        MD, title="指挥官月度迭代报告 2026-11", subject="v1 · 测试小院"
    )
    types = [b.type for b in model.blocks]
    assert "heading" in types and "bullets" in types and "numbered" in types
    assert model.blocks[0].type == "heading" and model.blocks[0].level == 1
    numbered = next(b for b in model.blocks if b.type == "numbered")
    assert numbered.items == ["建议一", "建议二"]
    assert any(b.type == "paragraph" and "普通段落" in b.text for b in model.blocks)


def test_markdown_to_doc_model_empty_fallback():
    model = commander_export.markdown_to_doc_model("", title="t")
    assert model.blocks and model.blocks[-1].text == "（报告内容为空）"


def test_markdown_long_paragraph_split():
    model = commander_export.markdown_to_doc_model("字" * 9000, title="t")
    assert all(len(b.text) <= 4000 for b in model.blocks)
    assert sum(len(b.text) for b in model.blocks) == 9000


def test_iteration_report_doc_shape():
    raw = commander_export.iteration_report_doc(
        "# 标题\n正文", month_key="2026-11", version_label="v3", yard_name="稻麦小院"
    )
    assert raw["title"].startswith("指挥官月度迭代报告")
    assert "稻麦小院" in raw["subject"]
    assert raw["blocks"]


def test_weekly_push_idempotent(db_session):
    yard = create_yard(db_session, name="周报推送小院")
    db_session.commit()
    report = reports.weekly_optimization(db_session, yard_id=yard.id)
    # 同一周重复生成：第二次不再推送（审计锚点）
    reports.weekly_optimization(db_session, yard_id=yard.id)
    rows = (
        db_session.query(__import__("app").modules.audit.models.AuditLog)
        .filter(
            __import__("app").modules.audit.models.AuditLog.yard_id == yard.id,
            __import__("app").modules.audit.models.AuditLog.action == "commander_weekly_pushed",
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].metadata_json.get("week_key") == report["week_key"] or rows[0].resource_id
    assert report["markdown"].startswith("# 每周优化报告")
