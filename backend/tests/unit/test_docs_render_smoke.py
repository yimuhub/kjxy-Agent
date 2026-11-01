"""文档渲染冒烟：开发环境能出合法 Office 文件；组件缺失时给可读错误与兜底。"""

from __future__ import annotations

import asyncio
import io
import json
import zipfile

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.chat import document as document_service
from app.modules.chat import service
from app.modules.docs import render_docx, render_pptx
from app.modules.docs.deck_plan import DeckPlan, DeckSlide
from app.modules.docs.model import DocBlock, DocModel
from app.modules.yards.repository import create_yard
from docx import Document
from pptx import Presentation


def _plan() -> DeckPlan:
    return DeckPlan(
        theme="tech_blue",
        title="冒烟演示",
        slides=[
            DeckSlide(type="cover", title="冒烟演示"),
            DeckSlide(type="agenda", title="目录", bullets=["背景", "方案"]),
            DeckSlide(type="bullets", title="要点", bullets=["第一条", "第二条"]),
            DeckSlide(type="statement", title="结论", key_message="渲染链路正常"),
            DeckSlide(type="closing", title="谢谢"),
        ],
    )


def _model() -> DocModel:
    return DocModel(
        title="冒烟文档",
        blocks=[
            DocBlock(type="heading", level=1, text="一、概述"),
            DocBlock(type="paragraph", text="这是一段用于冒烟测试的正文。"),
        ],
    )


def test_minimal_pptx_is_valid_zip_and_reopenable() -> None:
    data = render_pptx.to_pptx_plan(_plan())

    assert data.startswith(b"PK\x03\x04")
    assert zipfile.is_zipfile(io.BytesIO(data))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.testzip() is None
    prs = Presentation(io.BytesIO(data))
    assert len(prs.slides) == 5


def test_minimal_docx_is_valid_zip_and_reopenable() -> None:
    data = render_docx.to_docx(_model())

    assert data.startswith(b"PK\x03\x04")
    assert zipfile.is_zipfile(io.BytesIO(data))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.testzip() is None
    doc = Document(io.BytesIO(data))
    text = "\n".join(paragraph.text for paragraph in doc.paragraphs)
    assert "冒烟文档" in text or "一、概述" in text
    assert "这是一段用于冒烟测试的正文。" in text


def test_pptx_template_missing_becomes_readable_error(monkeypatch) -> None:
    """模拟安装包漏收 pptx 模板：FileNotFoundError → DOC_RENDER_FAILED/503。"""

    def _boom():
        raise FileNotFoundError("pptx/templates/notesMaster.xml")

    monkeypatch.setattr(render_pptx, "Presentation", _boom)
    with pytest.raises(AppError) as e:
        render_pptx.to_pptx_plan(_plan())
    assert e.value.code == "DOC_RENDER_FAILED"
    assert e.value.http_status == 503
    assert e.value.details == {"component": "pptx"}
    assert "请更新到最新版本" in e.value.message


def test_docx_template_missing_becomes_readable_error(monkeypatch) -> None:
    """模拟安装包漏收 docx 模板：FileNotFoundError → DOC_RENDER_FAILED/503。"""

    def _boom():
        raise FileNotFoundError("docx/templates/default.docx")

    monkeypatch.setattr(render_docx, "Document", _boom)
    with pytest.raises(AppError) as e:
        render_docx.to_docx(_model())
    assert e.value.code == "DOC_RENDER_FAILED"
    assert e.value.http_status == 503
    assert e.value.details == {"component": "docx"}
    assert "请更新到最新版本" in e.value.message


class _Resp:
    content = json.dumps(
        {
            "theme": "agri_green",
            "title": "智慧灌溉汇报",
            "slides": [
                {"type": "cover", "title": "智慧灌溉汇报"},
                {"type": "agenda", "title": "目录", "bullets": ["现状", "方案"]},
                {"type": "bullets", "title": "现状", "bullets": ["改造 12 个大棚"]},
                {"type": "statement", "title": "结论", "key_message": "节水率提升"},
                {"type": "closing", "title": "谢谢"},
            ],
        },
        ensure_ascii=False,
    )
    input_tokens = 11
    output_tokens = 22
    model = "stub"


async def _fake_complete(db, request):
    return _Resp()


def test_pptx_render_failure_falls_back_to_markdown_card(db_session, monkeypatch) -> None:
    """渲染组件缺失时不让异常冒泡，改为交付 Markdown 大纲并明确提示升级。"""
    yard = create_yard(db_session, name="PPT 兜底小院")
    db_session.flush()
    ctx = YardContext(yard_id=yard.id, user_id="u-fallback", roles=[], request_id="r-fallback")
    session = service.create_session(
        db_session, yard_id=yard.id, created_by="u-fallback", target="assistant"
    )
    monkeypatch.setattr(document_service, "complete_with_logging", _fake_complete)

    def _boom(plan, path=None):
        raise AppError(
            "DOC_RENDER_FAILED",
            "文档渲染组件不可用（安装包可能缺少模板资源），请更新到最新版本。",
            http_status=503,
            details={"component": "pptx"},
        )

    monkeypatch.setattr(document_service.render_pptx, "to_pptx_plan", _boom)
    card, note, _usage = asyncio.run(
        document_service.generate_document_from_chat(
            db_session,
            ctx=ctx,
            session_id=session.id,
            request_text="做个智慧灌溉汇报 PPT",
        )
    )
    assert card["format"] == "md"
    assert card["previewable"] is True
    assert "PPT 渲染组件缺失，已改为 Markdown 大纲，请更新应用" in note
    data, _mime, filename = document_service.docs_service.download_bytes(
        db_session, ctx=ctx, session_id=session.id, attachment_id=card["attachment_id"]
    )
    assert filename.endswith(".md")
    assert data.startswith(b"# ")
