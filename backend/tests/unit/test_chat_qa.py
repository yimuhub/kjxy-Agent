"""对话内知识问答（v3.13）：引用落库、资料不足诚实回答、会话边界。"""

import app.modules.agriculture.qa_agent as qa_agent
import pytest
from app.core.security import create_access_token
from app.modules.auth import repository as auth_repo
from app.modules.chat import service as chat_service
from app.modules.yards import repository as yard_repo


@pytest.fixture()
def world(db_session):
    yard = yard_repo.create_yard(db_session, name="问答小院")
    owner = auth_repo.create_user(db_session, username="qa-owner", password="pw-qa")
    other = auth_repo.create_user(db_session, username="qa-other", password="pw-qa")
    yard_repo.add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="operator")
    yard_repo.add_membership(db_session, yard_id=yard.id, user_id=other.id, role="operator")
    db_session.commit()
    session = chat_service.create_session(
        db_session, yard_id=yard.id, created_by=owner.id, title="问答会话"
    )
    db_session.commit()
    return {"yard": yard, "owner": owner, "other": other, "session": session}


def _hdr(user, yard) -> dict[str, str]:
    token = create_access_token(user_id=user.id, yard_id=yard.id, roles=["operator"])
    return {"Authorization": f"Bearer {token}"}


def _stub_knowledge(monkeypatch, hits):
    class _Registry:
        def get(self, name):
            if name != "knowledge.search":
                return None

            async def _tool(payload, context=None):
                return hits

            return _tool

    monkeypatch.setattr(qa_agent, "_tool_registry_override", _Registry())

    async def _fake_llm(req):
        class _R:
            text = "小麦返青期应结合浇水追肥。【来源1】"

        return _R()

    monkeypatch.setattr(qa_agent, "complete_with_logging", _fake_llm)

    class _Req:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(qa_agent, "LLMRequest", _Req)


HITS = [
    {
        "document_id": "doc-1",
        "chunk_id": "chunk-1",
        "title": "小麦春季管理技术",
        "source": "县农技站",
        "page_number": 3,
        "quote": "小麦返青期结合浇水每亩追施尿素10公斤。",
    }
]


def test_chat_qa_persists_answer_with_citations(client, db_session, world, monkeypatch):
    _stub_knowledge(monkeypatch, HITS)
    res = client.post(
        f"/api/v1/chat/sessions/{world['session'].id}/qa",
        json={"question": "小麦返青期怎么追肥？", "top_k": 5},
        headers=_hdr(world["owner"], world["yard"]),
    )
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["user_message"]["role"] == "user"
    assistant = data["assistant_message"]
    assert assistant["role"] == "assistant"
    assert assistant["routed_by"] == "qa"
    assert assistant["routed_label"] == "知识问答"
    assert len(assistant["citations"]) == 1
    assert assistant["citations"][0]["kind"] == "knowledge"
    assert assistant["citations"][0]["title"] == "小麦春季管理技术"

    history = client.get(
        f"/api/v1/chat/sessions/{world['session'].id}/messages",
        headers=_hdr(world["owner"], world["yard"]),
    )
    assert history.status_code == 200
    rows = history.json()["data"]["items"]
    assert [r["role"] for r in rows][-2:] == ["user", "assistant"]
    # 标题由首条用户消息生成
    assert world["session"].title


def test_chat_qa_without_hits_is_honest(client, db_session, world, monkeypatch):
    _stub_knowledge(monkeypatch, [])
    res = client.post(
        f"/api/v1/chat/sessions/{world['session'].id}/qa",
        json={"question": "一种从未记录过的作物病害怎么治？"},
        headers=_hdr(world["owner"], world["yard"]),
    )
    assert res.status_code == 200, res.text
    assistant = res.json()["data"]["assistant_message"]
    assert assistant["citations"] == []
    assert "知识库" in assistant["content"]
    assert "专家复核" in assistant["content"]
    assert assistant["payload"]["needs_expert_review"] is True


def test_chat_qa_is_owner_scoped(client, world):
    res = client.post(
        f"/api/v1/chat/sessions/{world['session'].id}/qa",
        json={"question": "越权提问"},
        headers=_hdr(world["other"], world["yard"]),
    )
    assert res.status_code == 404


def test_chat_qa_validates_input(client, world):
    res = client.post(
        f"/api/v1/chat/sessions/{world['session'].id}/qa",
        json={"question": "   "},
        headers=_hdr(world["owner"], world["yard"]),
    )
    # 全局校验器把字段错误统一收敛为 400 INVALID_REQUEST（见 core/errors.py）
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_REQUEST"
    bad = client.post(
        f"/api/v1/chat/sessions/{world['session'].id}/qa",
        json={"question": "问题", "top_k": 0},
        headers=_hdr(world["owner"], world["yard"]),
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "INVALID_REQUEST"
