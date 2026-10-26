"""声明式 Agent 运行集成测试（M2）。

发布后的声明式 Agent 通过现有 POST /agents/{code}/run 端到端运行：
知识检索命中 → 引用透传 → FakeLLM 确定性输出 → agent_run 审计 + llm_calls 记账。
"""

import pytest
from app.core.security import create_access_token
from app.modules.audit.models import AuditLog
from app.modules.knowledge.embedding import get_embedding_provider
from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument
from app.modules.llm.models import LLMCall
from app.modules.yards.repository import create_yard

DOC_CONTENT = "水稻晒田技术要点：群体茎蘖数达到预期穗数80%时及时晒田，控制无效分蘖。"

BODY = {
    "agent_code": "rice_helper",
    "name": "水稻种植助手",
    "description": "低风险水稻问答",
    "emoji": "🌾",
    "persona_prompt": "你是一名水稻种植技术助手。",
    "task_prompt_template": "请依据资料回答问题：{{question}}",
    "allowed_tools": ["knowledge.search"],
    "knowledge_scopes": ["yard"],
}


def _hdr(user_id: str = "u-run-admin", yard_id: str | None = None, roles=("yard_admin",)):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def yard(db_session):
    t = create_yard(db_session, name="运行测试小院")
    db_session.commit()
    return t


@pytest.fixture()
def other_yard(db_session):
    t = create_yard(db_session, name="隔壁小院")
    db_session.commit()
    return t


@pytest.fixture()
def seeded_knowledge(db_session, yard):
    """直接写入 ready 文档 + 带确定性 mock 向量的 chunk（查询原文必命中 top1）。"""
    embedding = get_embedding_provider().embed([DOC_CONTENT])[0]
    doc = KnowledgeDocument(
        yard_id=yard.id,
        title="水稻晒田资料",
        storage_key=f"decl-{yard.id}",
        mime_type="text/plain",
        source="upload",
        status="ready",
        checksum="0" * 64,
        uploaded_by="u-run-admin",
    )
    db_session.add(doc)
    db_session.flush()  # autoflush=False：先落 flush 拿 doc.id 再挂 chunk
    chunk = KnowledgeChunk(
        yard_id=yard.id,
        document_id=doc.id,
        chunk_index=0,
        content=DOC_CONTENT,
        page_number=1,
        trust_level="yard_material",
        embedding=list(embedding),
    )
    db_session.add(chunk)
    db_session.commit()
    return doc


def _create_and_publish(client, yard, body=None):
    created = client.post(
        "/api/v1/agents/declarative", json=body or BODY, headers=_hdr(yard_id=yard.id)
    )
    assert created.status_code == 200, created.text
    data = created.json()["data"]
    published = client.post(
        f"/api/v1/agents/declarative/{data['id']}/publish", headers=_hdr(yard_id=yard.id)
    )
    assert published.status_code == 200, published.text
    assert published.json()["data"]["status"] == "published"
    return data


def test_published_declarative_run_end_to_end(client, db_session, yard, seeded_knowledge):
    cfg = _create_and_publish(client, yard)

    resp = client.post(
        f"/api/v1/agents/{cfg['agent_code']}/run",
        json={"input": {"question": DOC_CONTENT, "top_k": 3}},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "succeeded"
    output = data["output"]
    assert output["kind"] == "declarative_agent"
    assert output["agent_code"] == "rice_helper"
    # FakeLLM（开发/CI 确定性 Mock）
    assert "Mock" in output["answer"]

    # 引用字段存在且来自真实检索（不伪造）
    assert len(data["citations"]) >= 1
    cite = data["citations"][0]
    assert cite["document_id"] == seeded_knowledge.id
    assert cite["chunk_id"]
    assert cite["quote"]
    assert cite["trust_level"] == "yard_material"

    # 记账：agent_run 审计 + llm_calls 行（complete_with_logging 落库）
    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "agent_run", AuditLog.yard_id == yard.id)
        .first()
    )
    assert audit is not None
    assert audit.result == "success"
    assert audit.metadata_json["kind"] == "declarative_agent"
    assert audit.metadata_json["citations"] == len(data["citations"])
    assert db_session.query(LLMCall).filter(LLMCall.yard_id == yard.id).count() == 1


def test_run_is_deterministic_with_fake_llm(client, yard, seeded_knowledge):
    _create_and_publish(client, yard)
    answers = []
    for _ in range(2):
        resp = client.post(
            f"/api/v1/agents/{'rice_helper'}/run",
            json={"input": {"question": DOC_CONTENT}},
            headers=_hdr(yard_id=yard.id),
        )
        assert resp.status_code == 200, resp.text
        answers.append(resp.json()["data"]["output"]["answer"])
    assert answers[0] == answers[1]  # FakeLLM 确定性


def test_run_requires_question_and_published_state(client, yard, seeded_knowledge):
    cfg = _create_and_publish(client, yard)

    empty_q = client.post(
        f"/api/v1/agents/{cfg['agent_code']}/run",
        json={"input": {"question": "  "}},
        headers=_hdr(yard_id=yard.id),
    )
    assert empty_q.status_code == 400
    assert empty_q.json()["error"]["code"] == "INVALID_REQUEST"

    # 停用后不可再运行
    client.post(f"/api/v1/agents/declarative/{cfg['id']}/deprecate", headers=_hdr(yard_id=yard.id))
    gone = client.post(
        f"/api/v1/agents/{cfg['agent_code']}/run",
        json={"input": {"question": DOC_CONTENT}},
        headers=_hdr(yard_id=yard.id),
    )
    assert gone.status_code == 404


def test_cross_yard_run_is_404(client, yard, other_yard, seeded_knowledge):
    cfg = _create_and_publish(client, yard)
    resp = client.post(
        f"/api/v1/agents/{cfg['agent_code']}/run",
        json={"input": {"question": DOC_CONTENT}},
        headers=_hdr("u-other", other_yard.id),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "AGENT_NOT_FOUND"


def test_platform_registry_behavior_unchanged(client, yard):
    """平台注册表优先：内置 code 不会落到声明式分支（行为不变）。"""
    resp = client.get("/api/v1/agents", headers=_hdr(yard_id=yard.id))
    assert resp.status_code == 200
    codes = {a["code"] for a in resp.json()["data"]["agents"]}
    assert {"orchestrator", "agriculture_qa"} <= codes
    # 声明式表为空时，未知 code 依旧 404（原行为不变）
    unknown = client.post(
        "/api/v1/agents/no_such_agent/run",
        json={"input": {}},
        headers=_hdr(yard_id=yard.id),
    )
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "AGENT_NOT_FOUND"
