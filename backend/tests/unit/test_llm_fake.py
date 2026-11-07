"""FakeProvider 与 LLM 调用记录测试。"""

import hashlib

import pytest
from app.core.errors import AppError
from app.modules.llm import models as llm_models  # noqa: F401 触发建表
from app.modules.llm.models import LLMCall
from app.modules.llm.providers_fake import FakeProvider, estimate_tokens
from app.modules.llm.schemas import ChatMessage, LLMRequest
from app.modules.llm.service import complete_with_logging


def _make_request(question: str = "水稻纹枯病怎么防治？", **kw) -> LLMRequest:
    metadata = {"yard_id": "t-001", "task_id": "task-1", "request_id": "req-test-1"}
    metadata.update(kw.pop("metadata", {}))
    return LLMRequest(
        messages=[ChatMessage(role="user", content=question)],
        metadata=metadata,
        **kw,
    )


@pytest.mark.asyncio
async def test_fake_response_is_deterministic():
    provider = FakeProvider()
    req = _make_request()
    r1 = await provider.complete(req)
    r2 = await provider.complete(req)
    assert r1.content == r2.content
    assert r1.content.startswith("【Mock 模型回答】")
    assert "水稻纹枯病怎么防治" in r1.content  # 包含问题摘要
    assert r1.finish_reason == "stop"


@pytest.mark.asyncio
async def test_fake_token_estimate_by_chars():
    provider = FakeProvider()
    req = _make_request(question="你好")
    resp = await provider.complete(req)
    input_text = "user:你好"
    assert resp.input_tokens == estimate_tokens(input_text) == max(1, len(input_text))
    assert resp.output_tokens == estimate_tokens(resp.content)
    assert 5 <= resp.latency_ms <= 50


@pytest.mark.asyncio
async def test_fake_plan_prompt_returns_complete_sections():
    """方案合成提示词要回放可解析的分章方案，否则 CI 只能看到「缺章」。"""
    from app.modules.chat.plan import SECTIONS, _missing_sections, _split_sections, _thin_sections

    system = "你是科技小院的方案合成器。\n" + "\n".join(f"## {name}" for name, _ in SECTIONS)
    req = LLMRequest(
        messages=[
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content="写一份小麦赤霉病的防治方案"),
        ],
        metadata={"yard_id": "t-001", "request_id": "req-plan"},
    )
    resp = await FakeProvider().complete(req)

    assert _missing_sections(resp.content) == []
    assert _thin_sections(_split_sections(resp.content)) == []
    assert len(_split_sections(resp.content)) == len(SECTIONS)


@pytest.mark.asyncio
async def test_fake_plain_prompt_keeps_generic_answer():
    """非方案请求不受影响，仍是统一的 Mock 文本。"""
    req = _make_request("水稻纹枯病怎么防治？")
    resp = await FakeProvider().complete(req)
    assert resp.content.startswith("【Mock 模型回答】")


@pytest.mark.asyncio
async def test_service_writes_success_row(db_session):
    req = _make_request()
    resp = await complete_with_logging(db_session, req)
    db_session.commit()

    row = db_session.query(LLMCall).one()
    assert row.status == "success"
    assert row.yard_id == "t-001"
    assert row.task_id == "task-1"
    assert row.provider == "fake"
    assert row.model == resp.model
    assert row.input_tokens == resp.input_tokens
    assert row.output_tokens == resp.output_tokens
    assert row.estimated_cost == 0.0  # fake 单价为 0
    assert row.error_category is None
    assert row.request_id == "req-test-1"
    # 只存 sha256 摘要，不存原文
    expected_sha = hashlib.sha256("user:水稻纹枯病怎么防治？".encode()).hexdigest()
    assert row.prompt_sha == expected_sha
    assert len(row.prompt_sha) == 64


class _FailingProvider:
    PRICE_PER_1K_USD = 0.0
    model = "fake-mock-1"

    async def complete(self, request):
        raise AppError("LLM_ERROR", "MOCK 超时", http_status=502, details={"category": "timeout"})


@pytest.mark.asyncio
async def test_service_writes_error_row_and_reraises(db_session, monkeypatch):
    import app.modules.llm.service as llm_service

    monkeypatch.setattr(
        llm_service,
        "get_provider_candidates",
        lambda: [(_FailingProvider(), "settings_fallback")],
    )
    req = _make_request()

    with pytest.raises(AppError) as exc_info:
        await complete_with_logging(db_session, req)
    assert exc_info.value.code == "LLM_ERROR"  # 原样抛出
    db_session.commit()

    row = db_session.query(LLMCall).one()
    assert row.status == "error"
    assert row.error_category == "timeout"
    assert row.input_tokens is None
    assert row.estimated_cost == 0.0
