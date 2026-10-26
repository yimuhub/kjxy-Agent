"""问答 Agent 单元测试：fake llm + stub knowledge.search tool。"""

import app.modules.agriculture.qa_agent as qa_agent
import pytest
from app.modules.agriculture.qa_agent import AgricultureQAAgent, QAAgentContext


def _ctx() -> QAAgentContext:
    return QAAgentContext(yard_id="t1", user_id="u1", roles=["member"], request_id="req_test")


class _StubRegistry:
    def __init__(self, tool):
        self._tool = tool

    def get(self, name: str):
        return self._tool if name == "knowledge.search" else None


def _search_tool(hits):
    async def _tool(payload: dict, context=None):
        assert context.yard_id == "t1"  # 小院上下文必须透传
        return hits

    return _tool


@pytest.fixture()
def fake_llm(monkeypatch):
    async def _fake(req):
        class _R:
            text = "根据资料，小麦返青期应及时追肥浇水。【来源1】"

        return _R()

    monkeypatch.setattr(qa_agent, "complete_with_logging", _fake)

    # LLMRequest 兼容类：测试中直接提供可构造的桩
    class _Req:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(qa_agent, "LLMRequest", _Req)
    return _fake


@pytest.fixture()
def hits():
    return [
        {
            "document_id": "doc1",
            "chunk_id": "c1",
            "title": "小麦春季管理技术",
            "source": "县农技站",
            "page_number": 3,
            "quote": "小麦返青期结合浇水每亩追施尿素10公斤。",
        },
        {
            "document_id": "doc1",
            "chunk_id": "c2",
            "title": "小麦春季管理技术",
            "source": "县农技站",
            "quote": "拔节期注意防治蚜虫。",
        },
        # 缺少 chunk_id 的脏数据：必须被丢弃，绝不能出现在引用里
        {"document_id": "docX", "title": "脏数据", "source": "未知", "quote": "伪造内容"},
    ]


async def test_qa_with_hits_citations_correct(monkeypatch, fake_llm, hits):
    monkeypatch.setattr(qa_agent, "_tool_registry_override", _StubRegistry(_search_tool(hits)))
    agent = AgricultureQAAgent()
    result = await agent.run({"question": "小麦返青期怎么管理？", "top_k": 5}, _ctx())
    assert result.status == "succeeded"
    out = result.output
    assert out["answer"]
    assert out["needs_expert_review"] is False
    # 引用必须全部来自本次检索，且与 hit 一一对应
    assert len(out["citations"]) == 2
    assert out["citations"][0]["document_id"] == "doc1"
    assert out["citations"][0]["chunk_id"] == "c1"
    assert out["citations"][0]["quote"] == "小麦返青期结合浇水每亩追施尿素10公斤。"
    assert out["citations"][0]["page_number"] == 3
    assert out["citations"][1]["chunk_id"] == "c2"
    # 脏数据未被伪造成引用
    assert all(c["chunk_id"] in ("c1", "c2") for c in out["citations"])
    assert result.warnings == []


async def test_qa_no_hits_needs_expert(monkeypatch, fake_llm):
    monkeypatch.setattr(qa_agent, "_tool_registry_override", _StubRegistry(_search_tool([])))
    agent = AgricultureQAAgent()
    result = await agent.run({"question": "火星上能种小麦吗？", "top_k": 5}, _ctx())
    assert result.status == "succeeded"
    out = result.output
    assert out["needs_expert_review"] is True
    assert out["citations"] == []
    assert "暂未找到" in out["answer"]
    assert any("仅供" in w for w in out["warnings"])


async def test_qa_high_risk_goes_to_expert(monkeypatch, fake_llm, hits):
    monkeypatch.setattr(qa_agent, "_tool_registry_override", _StubRegistry(_search_tool(hits)))
    agent = AgricultureQAAgent()
    result = await agent.run({"question": "小麦蚜虫打什么农药？剂量多少？", "top_k": 5}, _ctx())
    assert result.status == "succeeded"
    out = result.output
    assert out["needs_expert_review"] is True
    assert any("高风险" in w for w in out["warnings"])
    # 引用仍真实
    assert len(out["citations"]) == 2


async def test_qa_llm_unavailable_fallback(monkeypatch, hits):
    """llm 模块缺失时降级为检索原文摘要，不编造、不抛异常。"""
    monkeypatch.setattr(qa_agent, "complete_with_logging", None)
    monkeypatch.setattr(qa_agent, "LLMRequest", None)
    monkeypatch.setattr(qa_agent, "_tool_registry_override", _StubRegistry(_search_tool(hits)))
    agent = AgricultureQAAgent()
    result = await agent.run({"question": "小麦返青期怎么管理？"}, _ctx())
    assert result.status == "succeeded"
    out = result.output
    assert out["needs_expert_review"] is True
    assert "小麦返青期结合浇水每亩追施尿素10公斤。" in out["answer"]
    assert len(out["citations"]) == 2


async def test_qa_empty_question():
    agent = AgricultureQAAgent()
    result = await agent.run({"question": "   "}, _ctx())
    assert result.status == "failed"


def test_agent_registered_on_import():
    from app.modules.agriculture.qa_agent import get_global_registry

    registry = get_global_registry()
    agent = registry.get("agriculture_qa")
    assert agent is not None
    assert agent.code == "agriculture_qa"
    assert agent.version == "1.0.0"
