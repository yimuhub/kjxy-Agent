"""失败模型调用的计量留痕（用量口径护栏）。

业务请求最终失败时整个事务会回滚。若失败记录（llm_calls/llm_traces 的 error 行）
留在业务会话里，就会随回滚一起消失 —— 恰好是最需要留痕的场景最没数据：
运营看到的"调用量/失败率"与真实消耗对不上，坏掉的供应商也永远累计不到降级阈值。

本护栏断言：调用失败后即使业务事务回滚，error 行依然在库里。
"""

from __future__ import annotations

import pytest
from app.core.errors import AppError
from app.db.base import Base
from app.modules.llm import service as llm_service
from app.modules.llm.models import LLMCall, LLMTrace
from app.modules.llm.schemas import ChatMessage, LLMMetadata, LLMRequest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class _BoomProvider:
    provider_name = "supplier:boom"
    supplier_id = None
    model = "boom-model"
    PRICE_PER_1K_USD = 0.0

    async def complete(self, request):  # noqa: ANN001, ARG002
        raise AppError(
            "LLM_ERROR",
            "模拟上游失败",
            http_status=502,
            details={"category": "provider_5xx"},
        )


@pytest.fixture()
def durable_session(tmp_path):
    """文件型 SQLite：每个会话拿到独立连接，才能真正验证"独立事务不被回滚吞掉"。

    内存库（StaticPool）所有会话共用一条连接，隔离事务会与业务事务纠缠，
    测不出本护栏要防的东西。
    """
    engine = create_engine(
        f"sqlite:///{tmp_path / 'telemetry.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


async def test_failed_call_is_recorded_after_rollback(durable_session, monkeypatch):
    monkeypatch.setattr(llm_service, "get_provider_candidates", lambda: [(_BoomProvider(), "test")])
    request = LLMRequest(
        messages=[ChatMessage(role="user", content="触发一次失败调用")],
        metadata=LLMMetadata(yard_id="probe-yard-1", request_id="probe-req-1"),
    )

    with pytest.raises(AppError):
        await llm_service.complete_with_logging(durable_session, request)

    # 业务事务回滚（模拟请求失败后的真实结局）
    durable_session.rollback()

    calls = durable_session.query(LLMCall).filter(LLMCall.yard_id == "probe-yard-1").all()
    assert len(calls) == 1
    assert calls[0].status == "error"
    assert calls[0].error_category == "provider_5xx"
    assert calls[0].prompt_sha  # 只留摘要，不留原文

    traces = durable_session.query(LLMTrace).filter(LLMTrace.yard_id == "probe-yard-1").all()
    assert len(traces) == 1
    assert traces[0].status == "error"
    assert traces[0].error_text


async def test_failed_call_does_not_leak_full_prompt(durable_session, monkeypatch):
    """失败行遵守与成功行相同的隐私口径。

    - ``llm_calls.prompt_sha``：只存 sha256 摘要，不含任何原文；
    - ``llm_traces.prompt_digest``：``sha256 前 12 位|前 200 字``（既定口径，
      截断到 200 字，**不存全文**）。
    """
    monkeypatch.setattr(llm_service, "get_provider_candidates", lambda: [(_BoomProvider(), "test")])
    secret = "绝密提示词" * 100  # 远超 200 字，足以验证截断
    request = LLMRequest(
        messages=[ChatMessage(role="user", content=secret)],
        metadata=LLMMetadata(yard_id="probe-yard-2", request_id="probe-req-2"),
    )
    with pytest.raises(AppError):
        await llm_service.complete_with_logging(durable_session, request)
    durable_session.rollback()

    calls = durable_session.query(LLMCall).filter(LLMCall.yard_id == "probe-yard-2").all()
    assert len(calls) == 1
    # 只留 sha256 十六进制摘要
    assert len(calls[0].prompt_sha) == 64
    assert "绝密提示词" not in calls[0].prompt_sha

    traces = durable_session.query(LLMTrace).filter(LLMTrace.yard_id == "probe-yard-2").all()
    assert len(traces) == 1
    digest = traces[0].prompt_digest or ""
    assert digest.startswith("user:") is False  # 形如 "<sha12>|user:..."
    assert "|" in digest
    sha12, _, preview = digest.partition("|")
    assert len(sha12) == 12
    # 预览截断到 200 字，不含全文
    assert len(preview) <= 220
    assert secret not in digest
