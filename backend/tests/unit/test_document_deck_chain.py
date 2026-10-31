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
