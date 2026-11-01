"""PPT 文档链路端到端：模型给 DeckPlan → 校验 → 质检 → 渲染 → 附件/预览。"""

from __future__ import annotations

import asyncio
import json

from app.core.deps import YardContext
from app.modules.chat import document as document_service
from app.modules.chat import service
from app.modules.yards.repository import create_yard

DECK = {
    "theme": "agri_green",
    "title": "智慧灌溉汇报",
    "subtitle": "2026 年度进展",
    "audience": "理事会",
    "storyline": "现状—方案",
    "slides": [
        {"type": "cover", "title": "智慧灌溉汇报"},
        {"type": "agenda", "title": "目录", "bullets": ["现状", "方案", "行动"]},
        {"type": "bullets", "title": "现状", "bullets": ["改造 12 个大棚", "培训 126 人"]},
        {
            "type": "chart",
            "title": "用水量",
            "chart": {
                "type": "bar",
                "categories": ["5月", "6月"],
                "series": [{"name": "用水", "values": [320, 280]}],
            },
        },
        {"type": "closing", "title": "谢谢"},
    ],
}


class _Resp:
    content = json.dumps(DECK, ensure_ascii=False)
    input_tokens = 11
    output_tokens = 22
    model = "stub"


async def _fake_complete(db, request):
    return _Resp()


def test_pptx_chain_end_to_end(db_session, monkeypatch, tmp_path):
    yard = create_yard(db_session, name="PPT 链路小院")
    db_session.flush()
    ctx = YardContext(yard_id=yard.id, user_id="u-ppt", roles=[], request_id="r-ppt")
    session = service.create_session(
        db_session, yard_id=yard.id, created_by="u-ppt", target="assistant"
    )
    monkeypatch.setattr(document_service, "complete_with_logging", _fake_complete)
    card, note, usage = asyncio.run(
        document_service.generate_document_from_chat(
            db_session,
            ctx=ctx,
            session_id=session.id,
            request_text="做个智慧灌溉汇报 PPT",
        )
    )
    assert card["format"] == "pptx"
    assert card["previewable"] is True
    assert "结构质检" in note and "PPT" in note
    assert usage["total_tokens"] == 33
    data, mime, filename = document_service.docs_service.download_bytes(
        db_session, ctx=ctx, session_id=session.id, attachment_id=card["attachment_id"]
    )
    assert data.startswith(b"PK\x03\x04") and filename.endswith(".pptx")
    html = document_service.docs_service.preview_html(
        db_session, ctx=ctx, session_id=session.id, attachment_id=card["attachment_id"]
    )
    assert "智慧灌溉汇报" in html


REQUEST = "生成一份宣传小院的 PPT，重点突出智能监测和节水灌溉"
OUTLINE = """## 智能监测
- 会说话的探头：传感器监测土壤湿度与天气变化
- 数据自动打包：设备整理成图表，非专业人员也能看懂
## 节水灌溉
- 滴灌节水 30%–50%，水分利用率 90% 以上
- 水肥一体化：同步灌溉施肥，省工省肥
## 效益
- 节水 30%：按项目实测数据待补充
- 增产 10%–25%：视作物与地区差异
"""

BAD_DECK = {
    "theme": "tech_blue",
    "title": REQUEST,
    "slides": [
        {"type": "cover", "title": REQUEST},
        {"type": "agenda", "title": "目录", "bullets": [REQUEST]},
        {"type": "bullets", "title": REQUEST, "bullets": ["要点一", "要点二"]},
        {"type": "bullets", "title": REQUEST, "bullets": ["要点三", "要点四"]},
        {"type": "closing", "title": "谢谢"},
    ],
}

GOOD_DECK = {
    "theme": "agri_green",
    "title": "智慧小院宣传方案",
    "slides": [
        {"type": "cover", "title": "智慧小院：让农业更智慧"},
        {"type": "agenda", "title": "目录", "bullets": ["智能监测", "节水灌溉", "效益"]},
        {"type": "bullets", "title": "田间神经末梢", "bullets": ["传感器网络", "数据采集"]},
        {"type": "bullets", "title": "数字书包", "bullets": ["自动整理", "图表呈现"]},
        {"type": "bullets", "title": "贴心闹钟", "bullets": ["主动预警", "指令清晰"]},
        {"type": "bullets", "title": "精准滴灌", "bullets": ["节水 30%", "水肥一体"]},
        {"type": "closing", "title": "谢谢"},
    ],
}


class _SequenceComplete:
    """按调用顺序返回预设 Deck：先坏后好用于验证重写，重复坏用于验证兜底。"""

    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = payloads
        self.calls = 0

    async def __call__(self, db, request):
        payload = self.payloads[min(self.calls, len(self.payloads) - 1)]
        self.calls += 1
        resp = _Resp()
        resp.content = json.dumps(payload, ensure_ascii=False)
        return resp


def test_outline_fallback_builds_deck_from_upstream_markdown():
    model = document_service._fallback_model("pptx", REQUEST, OUTLINE)
    plan = document_service.plan_from_doc_model(model, allow_short=True)
    assert model.title == "宣传小院"
    assert len(plan.slides) >= 6
    assert any("节水" in s.title or "灌溉" in s.title for s in plan.slides)
    assert document_service._deck_quality_ok(plan, REQUEST, OUTLINE) is True


def test_deck_quality_rejects_request_title_and_duplicates():
    bad = document_service.parse_deck_plan(BAD_DECK)
    assert document_service._deck_quality_ok(bad, REQUEST, OUTLINE) is False
    assert document_service._deck_quality_ok(bad, REQUEST, "") is False
    good = document_service.parse_deck_plan(GOOD_DECK)
    assert document_service._deck_quality_ok(good, REQUEST, OUTLINE) is True


def test_bad_plan_retried_once_and_rescued(db_session, monkeypatch):
    stub = _SequenceComplete([BAD_DECK, GOOD_DECK])
    monkeypatch.setattr(document_service, "complete_with_logging", stub)
    ctx = YardContext(yard_id="yard-r", user_id="u-r", roles=[], request_id="r-r")
    plan, used, usage = asyncio.run(
        document_service._generate_deck_plan(db_session, ctx, request_text=REQUEST, context=OUTLINE)
    )
    assert stub.calls == 2
    assert used == "deck_builder_retry"
    assert plan.title == "智慧小院宣传方案"
    assert usage["total_tokens"] == 66


def test_still_bad_after_retry_falls_back_to_outline(db_session, monkeypatch):
    stub = _SequenceComplete([BAD_DECK, BAD_DECK])
    monkeypatch.setattr(document_service, "complete_with_logging", stub)
    ctx = YardContext(yard_id="yard-f", user_id="u-f", roles=[], request_id="r-f")
    plan, used, _usage = asyncio.run(
        document_service._generate_deck_plan(db_session, ctx, request_text=REQUEST, context=OUTLINE)
    )
    assert stub.calls == 2
    assert used == "deck_builder_fallback"
    assert plan.title == "宣传小院"
    assert len(plan.slides) >= 6
    assert document_service._deck_quality_ok(plan, REQUEST, OUTLINE) is True


def test_topic_skeleton_fallback_without_upstream_material():
    model = document_service._fallback_model("pptx", REQUEST, "")
    plan = document_service.plan_from_doc_model(model, allow_short=True)
    titles = [s.title for s in plan.slides]
    assert model.title == "宣传小院"
    assert len(plan.slides) >= 8
    assert "智能监测" in titles
    assert "节水灌溉" in titles
    assert document_service._deck_quality_ok(plan, REQUEST, "") is True


def test_json_outline_blocks_from_slide_payload():
    context = (
        "[子任务 3「策划 PPT 内容大纲」]\n```json\n"
        '{"slides":[{"title":"封面：智慧小院","content":["智能监测","节水灌溉"]},'
        '{"title":"核心痛点","content":["盲目漫灌水耗高","凭经验施肥效率低"]}]}\n```'
    )
    blocks = document_service._outline_blocks(context)
    texts = [b.get("text") for b in blocks if b.get("type") == "heading"]
    assert "智慧小院" in texts
    assert "核心痛点" in texts
    assert any(b.get("type") == "bullets" and "盲目漫灌水耗高" in b["items"] for b in blocks)


def test_outline_fallback_cleans_numbering_and_compacts():
    context = (
        "## 1. 智能监测\n- 传感器实时采集\n- 数据自动整理\n"
        "## 2. 节水灌溉\n- 滴灌节水三成\n- 水肥一体化\n"
        "## 3. 综合效益\n- 节水增产减污"
    )
    plan = document_service._fallback_deck(REQUEST, context)
    titles = [s.title for s in plan.slides]
    assert "1. 智能监测" not in titles
    assert "智能监测" in titles
    for current, following in zip(plan.slides, plan.slides[1:], strict=False):
        assert not (
            current.type == "section"
            and following.type == "bullets"
            and current.title == following.title
        )
    assert len(plan.slides) >= 5
