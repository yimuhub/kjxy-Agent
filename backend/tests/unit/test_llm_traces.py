"""llm_traces 观测（W4）：落行 / 隐私口径 / 聚合形状 / 小院边界。

- declarative run 后 trace 落行（purpose=declarative、agent_ref=agent_code）；
- 未透传 purpose 的调用落 platform；
- prompt_digest 只存 sha12|前 200 字（全文之外的标记绝不出现在 digest）；
- 错误调用落 status=error + error_text（fake provider 也落行）；
- /llm/usage/summary 按日×模型聚合形状稳定；
- 查询端点：非法 purpose 400、非管理员 403、跨小院看不到对方数据。
"""

import asyncio

import pytest
from app.core.errors import AppError
from app.core.security import create_access_token
from app.modules.llm.models import LLMTrace
from app.modules.llm.schemas import ChatMessage, LLMMetadata, LLMRequest
from app.modules.llm.service import complete_with_logging
from app.modules.yards.repository import create_yard

BODY = {
    "agent_code": "trace_helper",
    "name": "观测测试助手",
    "description": "低风险问答",
    "emoji": "📊",
    "persona_prompt": "你是观测测试助手。",
    "task_prompt_template": "请回答：{{question}}",
    "allowed_tools": [],
    "knowledge_scopes": ["yard"],
}

#: 200 字之外的尾巴标记：出现在 digest 里即隐私口径失守
SECRET_TAIL = "机密尾巴XYZ"


def _hdr(user_id: str = "u-trace", yard_id: str | None = None, roles=("yard_admin",)):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def yard(db_session):
    t = create_yard(db_session, name="观测小院")
    db_session.commit()
    return t


@pytest.fixture()
def other_yard(db_session):
    t = create_yard(db_session, name="隔壁观测小院")
    db_session.commit()
    return t


def _create_and_publish(client, yard):
    created = client.post("/api/v1/agents/declarative", json=BODY, headers=_hdr(yard_id=yard.id))
    assert created.status_code == 200, created.text
    agent_id = created.json()["data"]["id"]
    published = client.post(
        f"/api/v1/agents/declarative/{agent_id}/publish", headers=_hdr(yard_id=yard.id)
    )
    assert published.status_code == 200, published.text


def _run(client, yard, question="棚室温度多少？"):
    resp = client.post(
        "/api/v1/agents/trace_helper/run",
        json={"input": {"question": question}},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    return resp


def test_declarative_run_writes_trace_row(client, db_session, yard):
    _create_and_publish(client, yard)
    _run(client, yard)

    rows = db_session.query(LLMTrace).filter(LLMTrace.yard_id == yard.id).all()
    assert len(rows) == 1  # fake provider 也落观测行
    t = rows[0]
    assert t.purpose == "declarative"
    assert t.agent_ref == "trace_helper"
    assert t.status == "ok"
    assert t.run_id  # = 调用的 task_id/run_id
    assert t.latency_ms >= 0
    assert t.input_tokens is not None and t.output_tokens is not None  # fake 估算可得
    assert t.error_text is None
    assert t.provider_id is None  # 无供应商（settings 兜底 fake）→ null，不编造


def test_prompt_digest_privacy_shape(client, db_session, yard):
    """digest = sha12|≤200 字前缀；超过 200 字的正文尾巴绝不入库。"""
    _create_and_publish(client, yard)
    question = "开头标记ABC" + "啊" * 400 + SECRET_TAIL
    _run(client, yard, question=question)

    t = db_session.query(LLMTrace).filter(LLMTrace.yard_id == yard.id).one()
    assert t.prompt_digest is not None
    sha12, sep, prefix = t.prompt_digest.partition("|")
    assert sep == "|" and len(sha12) == 12
    assert len(prefix) <= 200
    assert SECRET_TAIL not in t.prompt_digest  # 200 字之外的内容被截断丢弃


def test_error_call_writes_error_trace(db_session, monkeypatch):
    class _FailingProvider:
        PRICE_PER_1K_USD = 0.0
        model = "fake-mock-1"

        async def complete(self, request):
            raise AppError("LLM_ERROR", "脚本注入超时", http_status=502, details={})

    import app.modules.llm.service as llm_service

    monkeypatch.setattr(llm_service, "get_provider", lambda: _FailingProvider())
    req = LLMRequest(
        messages=[ChatMessage(role="user", content="会失败的问题")],
        metadata=LLMMetadata(yard_id="t-err", request_id="req-err"),
    )
    with pytest.raises(AppError):
        asyncio.run(complete_with_logging(db_session, req))
    db_session.commit()

    t = db_session.query(LLMTrace).one()
    assert t.status == "error"
    assert "LLM_ERROR" in (t.error_text or "")
    assert t.input_tokens is None  # 取不到 → null，不编造


def test_platform_purpose_fallback(db_session):
    req = LLMRequest(
        messages=[ChatMessage(role="user", content="平台口径调用")],
        metadata=LLMMetadata(yard_id="t-plain", request_id="req-plain"),
    )
    asyncio.run(complete_with_logging(db_session, req))
    db_session.commit()
    t = db_session.query(LLMTrace).one()
    assert t.purpose == "platform"  # 调用方未透传 → platform
    assert t.agent_ref is None


def test_usage_summary_shape(client, db_session, yard):
    _create_and_publish(client, yard)
    _run(client, yard)

    resp = client.get("/api/v1/llm/usage/summary?days=7", headers=_hdr(yard_id=yard.id))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["days"] == 7
    assert len(data["items"]) >= 1
    item = data["items"][0]
    assert set(item) == {"day", "model", "calls", "tokens", "avg_latency_ms", "errors"}
    assert item["model"] == "fake-mock-1"
    assert item["calls"] == 1
    assert item["tokens"] > 0
    assert item["errors"] == 0


def test_traces_list_filters_and_validation(client, db_session, yard):
    _create_and_publish(client, yard)
    _run(client, yard)
    hdr = _hdr(yard_id=yard.id)

    resp = client.get("/api/v1/llm/traces?purpose=declarative", headers=hdr)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["count"] == 1
    assert data["items"][0]["purpose"] == "declarative"
    # 出参没有 prompt 全文字段（只有 digest）
    assert "prompt_text" not in data["items"][0]

    assert client.get("/api/v1/llm/traces?purpose=bogus", headers=hdr).status_code == 400
    assert client.get("/api/v1/llm/traces?limit=201", headers=hdr).status_code == 400


def test_traces_reject_non_admin(client, yard):
    resp = client.get("/api/v1/llm/traces", headers=_hdr(roles=("operator",), yard_id=yard.id))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_traces_cross_yard_sees_nothing(client, db_session, yard, other_yard):
    _create_and_publish(client, yard)
    _run(client, yard)

    resp = client.get("/api/v1/llm/traces", headers=_hdr("u-other", other_yard.id))
    assert resp.status_code == 200
    assert resp.json()["data"]["count"] == 0  # 别的小院的行一条都不出现
    summary = client.get("/api/v1/llm/usage/summary", headers=_hdr("u-other", other_yard.id))
    assert summary.json()["data"]["items"] == []
