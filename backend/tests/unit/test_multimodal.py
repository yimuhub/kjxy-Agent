"""图片理解（v3.11）：识别结果写回、数据边界与诚实降级。

语音转写的用例已随 v3.30 迁到 ``tests/unit/test_asr_service.py``。

计量护栏：图片理解是真实花钱的模型调用，必须走统一 LLM 链路
（写 llm_calls / 扣配额 / 写 llm_traces），否则成本不可见、超发无人拦。
"""

import io
from types import SimpleNamespace

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.chat import attachments, multimodal
from app.modules.chat.models import ATT_DOCUMENT, ATT_IMAGE
from app.modules.llm.models import LLMCall, LLMTrace
from app.modules.llm.schemas import LLMResponse
from fastapi import UploadFile

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _ctx(yard_id: str = "t1", user_id: str = "u1") -> YardContext:
    return YardContext(yard_id=yard_id, user_id=user_id, roles=["yard_admin"], request_id="r1")


def _provider(base_url: str = "http://127.0.0.1:9/v1", model: str = "vision-x"):
    return SimpleNamespace(base_url=base_url, api_key="test-key", model=model)


async def test_recognize_image_writes_back_text(db_session, monkeypatch):
    att = attachments.upload_attachment(
        db_session,
        ctx=_ctx(),
        session_id="sess-1",
        file=UploadFile(file=io.BytesIO(PNG_BYTES), filename="leaf.png"),
    )
    assert att.kind == ATT_IMAGE and att.extracted_text == ""

    monkeypatch.setattr(multimodal, "get_provider", lambda: _provider())

    async def fake_vision(db, ctx, data_url, model):
        assert data_url.startswith("data:image/png;base64,")
        assert "不确定" in multimodal.VISION_PROMPT
        return "叶片出现梭形病斑，边缘褐色；图片文字“试验田 A3”"

    monkeypatch.setattr(multimodal, "_run_vision", fake_vision)
    result = await multimodal.recognize_image(db_session, _ctx(), att)
    assert result["chars"] > 0
    assert att.extracted_text.startswith("[图片理解]")
    assert "梭形病斑" in att.extracted_text

    context = attachments.build_attachment_context([att])
    assert "梭形病斑" in context
    assert "不可信数据" in context


async def test_recognize_image_is_metered(db_session, monkeypatch):
    """图片理解必须落 llm_calls / llm_traces（记账），否则成本不可见、超发无人拦。"""
    from app.modules.llm import service as llm_service

    att = attachments.upload_attachment(
        db_session,
        ctx=_ctx(),
        session_id="sess-1",
        file=UploadFile(file=io.BytesIO(PNG_BYTES), filename="leaf.png"),
    )
    monkeypatch.setattr(multimodal, "get_provider", lambda: _provider())

    seen: dict = {}

    class _FakeProvider:
        provider_name = "supplier:test"
        supplier_id = None
        model = "vision-x"
        PRICE_PER_1K_USD = 0.001

        async def complete(self, request):  # noqa: ANN001
            seen["request"] = request
            return LLMResponse(
                content="叶片出现梭形病斑",
                input_tokens=100,
                output_tokens=20,
                model="vision-x",
                latency_ms=12,
            )

    monkeypatch.setattr(llm_service, "get_provider_candidates", lambda: [(_FakeProvider(), "test")])

    result = await multimodal.recognize_image(db_session, _ctx(), att)
    assert "梭形病斑" in result["text"]

    # 计量侧：调用记录 + 观测轨迹都落库，用途码可单独核算
    calls = db_session.query(LLMCall).filter(LLMCall.purpose == multimodal.VISION_PURPOSE).all()
    assert len(calls) == 1
    assert calls[0].input_tokens == 100 and calls[0].output_tokens == 20
    assert calls[0].status == "success"

    traces = db_session.query(LLMTrace).filter(LLMTrace.purpose == multimodal.VISION_PURPOSE).all()
    assert len(traces) == 1 and traces[0].status == "ok"

    # 请求侧：走的是多模态内容数组，且带上了图片
    request = seen["request"]
    content = request.messages[0].content
    assert isinstance(content, list)
    assert content[0]["type"] == "text" and "不确定" in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_recognize_rejects_document(db_session):
    att = attachments.upload_attachment(
        db_session,
        ctx=_ctx(),
        session_id="sess-1",
        file=UploadFile(file=io.BytesIO(b"# title"), filename="note.md"),
    )
    assert att.kind == ATT_DOCUMENT
    with pytest.raises(AppError) as exc:
        await multimodal.recognize_image(db_session, _ctx(), att)
    assert exc.value.code == "INVALID_REQUEST"


async def test_recognize_without_provider_is_503(db_session):
    att = attachments.upload_attachment(
        db_session,
        ctx=_ctx(),
        session_id="sess-1",
        file=UploadFile(file=io.BytesIO(PNG_BYTES), filename="leaf.png"),
    )
    with pytest.raises(AppError) as exc:
        await multimodal.recognize_image(db_session, _ctx(), att)
    assert exc.value.code == "MULTIMODAL_NOT_CONFIGURED"
    assert exc.value.http_status == 503
