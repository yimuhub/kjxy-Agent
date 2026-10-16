"""问答全流程集成测试：POST /qa/answers sync 全链路。

knowledge 模块尚未交付，测试内以"真实注册"方式提供 knowledge.search 工具：
用测试本地表写入 chunk 数据（模拟 knowledge service 层写入），
经 qa_agent 的工具注册表解析执行，再调接口验证端到端。
"""

import app.modules.agriculture.qa_agent as qa_agent
import pytest
from app.core.deps import get_db_session
from app.core.security import create_access_token
from app.db.base import Base, TimestampMixin
from app.main import create_app
from app.modules.agriculture.router import router as qa_router
from app.modules.knowledge.embedding import get_embedding_provider
from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument
from fastapi.testclient import TestClient
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column


class QaChunk(Base, TimestampMixin):
    """测试用知识片段表（模拟 knowledge service 写入的 chunk 数据）。"""

    __tablename__ = "test_qa_chunks"

    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)


def _keyword_search_tool(db):
    """基于关键词交集的真实可运行的 knowledge.search 实现（测试替身）。"""

    async def _tool(payload: dict, context=None):
        question = str(payload.get("question", ""))
        top_k = int(payload.get("top_k", 5))
        tenant_id = getattr(context, "tenant_id", None)
        rows = db.query(QaChunk).filter(QaChunk.tenant_id == tenant_id).all()
        scored = []
        for r in rows:
            tokens = {question[i : i + 2] for i in range(len(question) - 1)}
            tokens = {t for t in tokens if len(t.strip()) == 2}
            score = sum(1 for t in tokens if t in r.content)
            if score > 0:
                scored.append((score, r))
        scored.sort(key=lambda x: -x[0])
        return [
            {
                "document_id": r.document_id,
                "chunk_id": r.id,
                "title": r.title,
                "source": r.source,
                "page_number": None,
                "quote": r.content,
            }
            for _, r in scored[:top_k]
        ]

    return _tool


class _StubRegistry:
    def __init__(self, tool):
        self._tool = tool

    def get(self, name: str):
        return self._tool if name == "knowledge.search" else None


@pytest.fixture()
def qa_client(db_session):
    app = create_app()
    app.include_router(qa_router, prefix="/api/v1")

    def override_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db_session] = override_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def auth_headers():
    token = create_access_token(user_id="u1", tenant_id="t1", roles=["member"])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def seed_chunks(db_session):
    db_session.add(
        QaChunk(
            tenant_id="t1",
            document_id="doc-wheat",
            title="小麦春季田间管理",
            source="县农技推广站",
            content="小麦返青期结合浇水，每亩追施尿素10公斤，促进早发稳长。",
        )
    )
    db_session.add(
        QaChunk(
            tenant_id="t1",
            document_id="doc-corn",
            title="玉米播种技术",
            source="县农技推广站",
            content="玉米播种深度以3到5厘米为宜，墒情差时可适当加深。",
        )
    )
    # 其他租户的数据：必须被隔离
    db_session.add(
        QaChunk(
            tenant_id="t2",
            document_id="doc-other",
            title="他租户资料",
            source="他处",
            content="小麦返青期每亩追施尿素50公斤（错误示范数据）。",
        )
    )
    db_session.commit()


@pytest.fixture()
def stub_tool_registry(db_session, monkeypatch, seed_chunks):
    monkeypatch.setattr(
        qa_agent, "_tool_registry_override", _StubRegistry(_keyword_search_tool(db_session))
    )

    async def _fake_llm(req):
        class _R:
            text = "根据资料，小麦返青期应结合浇水追施尿素。【来源1】"

        return _R()

    class _Req:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(qa_agent, "complete_with_logging", _fake_llm)
    monkeypatch.setattr(qa_agent, "LLMRequest", _Req)


def test_qa_flow_sync(qa_client, auth_headers, stub_tool_registry, db_session):
    resp = qa_client.post(
        "/api/v1/qa/answers",
        json={"question": "小麦返青期如何追肥？", "top_k": 5, "mode": "sync"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["answer"]
    assert data["needs_expert_review"] is False
    # 只命中本租户的小麦片段，玉米与他租户数据未被引用
    assert len(data["citations"]) == 1
    c = data["citations"][0]
    assert c["document_id"] == "doc-wheat"
    assert "每亩追施尿素10公斤" in c["quote"]
    # 引用真实性：chunk_id 必须存在于测试表中
    ids = {r.id for r in db_session.query(QaChunk).all()}
    assert c["chunk_id"] in ids


def test_qa_flow_high_risk(qa_client, auth_headers, stub_tool_registry):
    resp = qa_client.post(
        "/api/v1/qa/answers",
        json={"question": "小麦蚜虫用什么农药？剂量多少？", "mode": "sync"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["needs_expert_review"] is True
    assert any("高风险" in w for w in data["warnings"])


def test_qa_flow_no_hit(qa_client, auth_headers, stub_tool_registry):
    resp = qa_client.post(
        "/api/v1/qa/answers",
        json={"question": "水稻螟虫怎么防治？", "mode": "sync"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["needs_expert_review"] is True
    assert data["citations"] == []
    assert "暂未找到" in data["answer"]


def test_qa_flow_async_creates_task(
    qa_client, auth_headers, stub_tool_registry, monkeypatch, db_session
):
    """mode=async 时经 TaskService 创建 knowledge_qa 任务并返回任务 ID。"""
    import app.modules.agriculture.router as qa_router

    monkeypatch.setattr(qa_router, "enqueue_task", lambda task_id: True)  # 避免测试等待 redis
    resp = qa_client.post(
        "/api/v1/qa/answers",
        json={"question": "小麦返青期如何追肥？", "mode": "async"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["mode"] == "async"
    assert data["task_id"]
    assert data["enqueued"] is True
    # 任务行真实落库且 task_type 正确
    from app.modules.tasks.models import Task

    task = db_session.query(Task).filter(Task.id == data["task_id"]).one()
    assert task.task_type == "knowledge_qa"
    assert task.tenant_id == "t1"
    assert task.status == "queued"


def test_qa_flow_async_degraded_to_sync(qa_client, auth_headers, stub_tool_registry, monkeypatch):
    """tasks 服务不可用时 async 回退同步，并在 warnings 中诚实注明。"""
    import app.modules.agriculture.router as qa_router

    monkeypatch.setattr(qa_router, "TaskService", None)
    resp = qa_client.post(
        "/api/v1/qa/answers",
        json={"question": "小麦返青期如何追肥？", "mode": "async"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["answer"]
    assert any("降级为同步" in w for w in data["warnings"])


def test_qa_flow_real_knowledge_search(qa_client, auth_headers, db_session):
    """全真实路径：knowledge.service.search（向量检索）+ llm fake provider，全程无 stub。"""
    provider = get_embedding_provider()
    content = "小麦返青期结合浇水，每亩追施尿素10公斤，促进早发稳长。"
    doc = KnowledgeDocument(
        tenant_id="t1",
        title="小麦春季田间管理",
        storage_key="test/seed.md",
        mime_type="text/markdown",
        source="upload",
        status="ready",
        checksum="seed",
        uploaded_by="u1",
    )
    db_session.add(doc)
    db_session.flush()
    db_session.add(
        KnowledgeChunk(
            tenant_id="t1",
            document_id=doc.id,
            chunk_index=0,
            content=content,
            page_number=2,
            trust_level="tenant_material",
            embedding=provider.embed([content])[0],
        )
    )
    db_session.commit()

    resp = qa_client.post(
        "/api/v1/qa/answers",
        json={"question": "小麦返青期如何追肥？", "top_k": 5, "mode": "sync"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    # 真实 llm fake provider 生成答案
    assert data["answer"]
    assert data["needs_expert_review"] is False
    # 引用来自真实知识库检索，非伪造
    assert len(data["citations"]) == 1
    c = data["citations"][0]
    assert c["document_id"] == doc.id
    assert "每亩追施尿素10公斤" in c["quote"]
    assert c["page_number"] == 2


def test_qa_requires_auth(qa_client, stub_tool_registry):
    resp = qa_client.post("/api/v1/qa/answers", json={"question": "小麦？"})
    assert resp.status_code == 401
