"""Agent 工厂 API 集成测试（M3）：generate → 里程碑 → completed → apply 分级发布。

- 文件型 SQLite + 覆盖 worker 会话工厂：API 会话与 worker 会话各持独立连接
  （与 test_task_stream 同一并发安全模式），worker 同步执行 run_task_inline。
- LLM 用 FakeProvider（确定性非 JSON 输出）→ 蓝图必然落启发式兜底，
  generated_by 诚实标注 heuristic；里程碑以 TaskEvent（SSE 读取的同一张表）断言。
"""

from types import SimpleNamespace

import pytest
from app.core.deps import get_db_session
from app.core.security import create_access_token
from app.db.base import Base
from app.main import create_app
from app.modules.agents import factory_pipeline
from app.modules.agents.factory_models import FactoryRun
from app.modules.agents.models import YardAgent
from app.modules.audit.models import AuditLog
from app.modules.knowledge.embedding import get_embedding_provider
from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument
from app.modules.tasks import worker_tasks
from app.modules.tasks.models import Task
from app.modules.yards.repository import create_yard
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

BRIEF = "农机服务，侧重果园小型农机"
FOCUS = "侧重农机服务"


def _hdr(user_id="u-fac-1", yard_id=None, roles=("yard_admin",)):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'factory-test.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    yield factory
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def patch_worker_session(session_factory):
    worker_tasks._session_factory = session_factory
    yield
    worker_tasks._session_factory = None


@pytest.fixture()
def api_client(session_factory):
    app = create_app()

    def override_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_db
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture()
def yard(session_factory):
    db = session_factory()
    try:
        t = create_yard(db, name="工厂小院")
        db.commit()
        return SimpleNamespace(id=t.id)
    finally:
        db.close()


@pytest.fixture()
def other_yard(session_factory):
    db = session_factory()
    try:
        t = create_yard(db, name="隔壁小院")
        db.commit()
        return SimpleNamespace(id=t.id)
    finally:
        db.close()


def _seed_knowledge(session_factory, yard_id, texts):
    """写入 ready 文档 + mock 向量 chunk：查询原文必命中 top1。"""
    embedding = get_embedding_provider()
    db = session_factory()
    try:
        doc = KnowledgeDocument(
            yard_id=yard_id,
            title="工厂知识",
            storage_key=f"fac-{yard_id}",
            mime_type="text/plain",
            source="upload",
            status="ready",
            checksum="0" * 64,
            uploaded_by="u-fac-1",
        )
        db.add(doc)
        db.flush()
        for i, text in enumerate(texts):
            db.add(
                KnowledgeChunk(
                    yard_id=yard_id,
                    document_id=doc.id,
                    chunk_index=i,
                    content=text,
                    page_number=1,
                    trust_level="yard_material",
                    embedding=list(embedding.embed([text])[0]),
                )
            )
        db.commit()
        return SimpleNamespace(id=doc.id)
    finally:
        db.close()


def _generate(api_client, yard, brief=BRIEF, focus=FOCUS, headers=None):
    body = {"industry_brief": brief}
    if focus is not None:
        body["focus"] = focus
    return api_client.post(
        "/api/v1/agents/factory/generate",
        json=body,
        headers=headers or _hdr(yard_id=yard.id),
    )


def _run_worker(task_id):
    worker_tasks.run_task_inline(task_id)
    db = worker_tasks._get_session_factory()()
    try:
        return db.get(Task, task_id)
    finally:
        db.close()


def _completed_run(api_client, yard, headers=None):
    """generate + worker 同步执行到 completed，返回 (run_id, task_id)。"""
    resp = _generate(api_client, yard, headers=headers)
    assert resp.status_code == 202, resp.text
    ids = resp.json()["data"]
    task = _run_worker(ids["task_id"])
    assert task.status == "succeeded", (task.status, task.error_message)
    return ids["run_id"], ids["task_id"]


def _run_detail(api_client, run_id, yard, headers=None):
    resp = api_client.get(
        f"/api/v1/agents/factory/runs/{run_id}", headers=headers or _hdr(yard_id=yard.id)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_generate_completes_blueprint_milestones_and_audit(api_client, session_factory, yard):
    run_id, task_id = _completed_run(api_client, yard)

    data = _run_detail(api_client, run_id, yard)
    assert data["status"] == "completed"
    # FakeLLM 输出非 JSON → 必须诚实落 heuristic，不得伪报 llm
    assert data["generated_by"] == "heuristic"
    assert data["error"] is None and data["created_by"] == "u-fac-1"
    bp = data["blueprint_json"]
    assert bp["schema"] == "agent_factory_blueprint.v1"
    assert bp["focus"] == FOCUS
    agents = bp["agents"]
    assert 3 <= len(agents) <= 6  # 框架选角：按描述组合 3-6 个角色
    # 工作流与阶段：审核唯一且最后，阶段顺序稳定
    assert bp["workflow"]["mode"] == "sequential"
    assert [s["key"] for s in bp["workflow"]["stages"]] == sorted(
        [s["key"] for s in bp["workflow"]["stages"]],
        key=["analysis", "plan", "execution", "delivery", "review"].index,
    )
    assert agents[-1]["stage"] == "review"
    # 测试环境默认关闭联网情报：必须诚实标注未启用，而不是伪报
    assert bp["research"]["used"] is False
    assert bp["research"]["reason"]
    for item in agents:
        for field in (
            "agent_code",
            "name",
            "emoji",
            "description",
            "persona_prompt",
            "task_prompt_template",
            "allowed_tools",
            "knowledge_scopes",
            "suggested_questions",
            "agent_id",
            "knowledge_coverage",
            "needs_knowledge",
            "smoke_questions",
            "risk_level",
            "status",
        ):
            assert field in item, (item.get("agent_code"), field)
        assert item["knowledge_coverage"] == 0  # 本用例未播知识
        assert item["needs_knowledge"] is True
        assert item["status"] == "draft"
        assert item["risk_level"] == "low"
        assert 1 <= len(item["smoke_questions"]) <= 3

    # 草稿真的落库（created_from=factory）
    db = worker_tasks._get_session_factory()()
    try:
        drafts = db.query(YardAgent).filter(YardAgent.yard_id == yard.id).all()
        assert {i["agent_id"] for i in agents} == {a.id for a in drafts}
        assert all(a.created_from == "factory" and a.status == "draft" for a in drafts)
    finally:
        db.close()

    # 里程碑：SSE 读取的同一张 task_events 表可读 planning/generating/completed
    events = api_client.get(f"/api/v1/tasks/{task_id}/events", headers=_hdr(yard_id=yard.id))
    assert events.status_code == 200
    milestones = [
        e["payload_json"]["milestone"]
        for e in events.json()["data"]
        if e["event_type"] == "milestone"
    ]
    assert milestones == ["planning", "blueprint_ready", "generating", "completed"]

    # 审计：factory_generate + factory_run_completed
    db = worker_tasks._get_session_factory()()
    try:
        actions = {
            row[0]
            for row in db.query(AuditLog.action)
            .filter(AuditLog.yard_id == yard.id, AuditLog.action.like("factory_%"))
            .all()
        }
        assert {"factory_generate", "factory_run_completed"} <= actions
    finally:
        db.close()


def test_sse_stream_frames_contain_milestones(api_client, yard):
    _, task_id = _completed_run(api_client, yard)
    with api_client.stream(
        "GET", f"/api/v1/tasks/{task_id}/events/stream", headers=_hdr(yard_id=yard.id)
    ) as stream:
        body = "".join(stream.iter_text())
    assert "event: stream-init" in body
    assert "event: task-event" in body
    assert "milestone" in body and "generating" in body
    assert "event: stream-end" in body


def test_coverage_probe_with_seeded_knowledge(api_client, session_factory, yard):
    from app.modules.agents.factory_blueprint import heuristic_blueprint

    texts = sorted(
        {q for item in heuristic_blueprint(BRIEF, FOCUS) for q in item["suggested_questions"]}
    )
    _seed_knowledge(session_factory, yard.id, texts)

    run_id, _ = _completed_run(api_client, yard)
    agents = _run_detail(api_client, run_id, yard)["blueprint_json"]["agents"]
    assert all(i["knowledge_coverage"] >= 1 for i in agents)
    assert all(i["needs_knowledge"] is False for i in agents)


def test_apply_publish_low_risk_then_answer_with_citations(api_client, session_factory, yard):
    from app.modules.agents.factory_blueprint import heuristic_blueprint

    texts = sorted(
        {q for item in heuristic_blueprint(BRIEF, FOCUS) for q in item["suggested_questions"]}
    )
    docs = _seed_knowledge(session_factory, yard.id, texts)

    run_id, _ = _completed_run(api_client, yard)
    agents = _run_detail(api_client, run_id, yard)["blueprint_json"]["agents"]
    chosen = agents[0]

    resp = api_client.post(
        f"/api/v1/agents/factory/runs/{run_id}/apply",
        json={"codes": [chosen["agent_code"]], "publish": True},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["publish"] is True and body["requested"] == 1 and body["applied"] == 1
    assert body["published"] == 1 and body["failed"] == 0
    result = body["results"][0]
    assert result["ok"] is True and result["status"] == "published"
    assert result["agent_id"] == chosen["agent_id"]

    # approved_by 留痕 + factory_apply 审计
    detail = _run_detail(api_client, run_id, yard)
    assert detail["approved_by"] == "u-fac-1"
    db = worker_tasks._get_session_factory()()
    try:
        assert (
            db.query(AuditLog)
            .filter(AuditLog.action == "factory_apply", AuditLog.yard_id == yard.id)
            .count()
            == 1
        )
    finally:
        db.close()

    # 发布后可直接运行：回答带真实引用（FakeLLM + 小院知识检索）
    answer = api_client.post(
        f"/api/v1/agents/{chosen['agent_code']}/run",
        json={"input": {"question": texts[0], "top_k": 3}},
        headers=_hdr(yard_id=yard.id),
    )
    assert answer.status_code == 200, answer.text
    payload = answer.json()["data"]
    assert payload["status"] == "succeeded"
    assert "Mock" in payload["output"]["answer"]
    assert payload["citations"] and payload["citations"][0]["document_id"] == docs.id


def test_apply_publish_false_keeps_draft(api_client, session_factory, yard):
    run_id, _ = _completed_run(api_client, yard)
    agents = _run_detail(api_client, run_id, yard)["blueprint_json"]["agents"]
    codes = [i["agent_code"] for i in agents[:2]]

    resp = api_client.post(
        f"/api/v1/agents/factory/runs/{run_id}/apply",
        json={"codes": codes, "publish": False},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["draft"] == 2 and body["published"] == 0
    assert all(r["status"] == "draft" for r in body["results"])
    detail = _run_detail(api_client, run_id, yard)
    assert detail["approved_by"] is None  # 只留草稿不算审批动作


def test_apply_unknown_code_reports_per_item_without_breaking_others(
    api_client, session_factory, yard
):
    run_id, _ = _completed_run(api_client, yard)
    agents = _run_detail(api_client, run_id, yard)["blueprint_json"]["agents"]
    resp = api_client.post(
        f"/api/v1/agents/factory/runs/{run_id}/apply",
        json={"codes": ["no_such_code", agents[0]["agent_code"]], "publish": True},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["failed"] == 1 and body["applied"] == 1 and body["published"] == 1
    assert body["results"][0]["error"] == "code_not_in_blueprint"


def test_apply_cross_yard_404(api_client, session_factory, yard, other_yard):
    run_id, _ = _completed_run(api_client, yard)
    resp = api_client.post(
        f"/api/v1/agents/factory/runs/{run_id}/apply",
        json={"codes": ["anything"], "publish": True},
        headers=_hdr(user_id="u-other", yard_id=other_yard.id),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "FACTORY_RUN_NOT_FOUND"
    # 列表/详情同样小院隔离
    listed = api_client.get(
        "/api/v1/agents/factory/runs", headers=_hdr(user_id="u-other", yard_id=other_yard.id)
    )
    assert listed.json()["data"]["runs"] == []


def test_apply_limited_to_creator_or_yard_admin(api_client, session_factory, yard):
    run_id, _ = _completed_run(api_client, yard)
    agents = _run_detail(api_client, run_id, yard)["blueprint_json"]["agents"]
    payload = {"codes": [agents[0]["agent_code"]], "publish": False}

    # 同小院普通成员（非创建者）→ 403
    denied = api_client.post(
        f"/api/v1/agents/factory/runs/{run_id}/apply",
        json=payload,
        headers=_hdr(user_id="u-plain", yard_id=yard.id, roles=("operator",)),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "FORBIDDEN"

    # 同小院另一位 yard_admin（非创建者）→ 放行
    allowed = api_client.post(
        f"/api/v1/agents/factory/runs/{run_id}/apply",
        json=payload,
        headers=_hdr(user_id="u-fac-2", yard_id=yard.id),
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["data"]["applied"] == 1


def test_apply_requires_completed_run(api_client, session_factory, yard):
    resp = _generate(api_client, yard)
    run_id = resp.json()["data"]["run_id"]
    # 未经 worker 执行：run 仍是 claimed → 409 不可采纳
    apply_resp = api_client.post(
        f"/api/v1/agents/factory/runs/{run_id}/apply",
        json={"codes": ["x"], "publish": False},
        headers=_hdr(yard_id=yard.id),
    )
    assert apply_resp.status_code == 409
    assert apply_resp.json()["error"]["code"] == "FACTORY_RUN_NOT_READY"


def test_generate_validation_and_gate(api_client, yard):
    missing = api_client.post(
        "/api/v1/agents/factory/generate",
        json={"focus": "无行业描述"},
        headers=_hdr(yard_id=yard.id),
    )
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "INVALID_REQUEST"

    blank = api_client.post(
        "/api/v1/agents/factory/generate",
        json={"industry_brief": "   "},
        headers=_hdr(yard_id=yard.id),
    )
    assert blank.status_code == 400

    forbidden = api_client.post(
        "/api/v1/agents/factory/generate",
        json={"industry_brief": BRIEF},
        headers=_hdr(user_id="u-plain", yard_id=yard.id, roles=("operator",)),
    )
    assert forbidden.status_code == 403


def test_research_flows_into_blueprint_and_steps(api_client, yard, monkeypatch):
    """联网情报成功时：落库可溯源来源、增强 persona、步骤流留痕。"""
    monkeypatch.delenv("FACTORY_RESEARCH_DISABLED", raising=False)

    async def fake_search(db, yard_id, query, count=5, lang="zh-CN"):
        return {
            "query": query,
            "provider": "bing",
            "results": [
                {
                    "title": "果园小型农机选型指南",
                    "url": "https://example.com/machine",
                    "snippet": "按果园坡度与行距选型",
                    "source": "bing",
                    "published": None,
                }
            ],
        }

    monkeypatch.setattr("app.modules.search.service.search_web", fake_search)
    run_id, _ = _completed_run(api_client, yard)
    data = _run_detail(api_client, run_id, yard)
    bp = data["blueprint_json"]
    assert bp["research"]["used"] is True
    assert bp["research"]["provider"] == "bing"
    assert bp["research"]["sources"][0]["url"] == "https://example.com/machine"
    assert any("近期行业公开资料" in a["persona_prompt"] for a in bp["agents"])

    steps = api_client.get(
        f"/api/v1/agents/declarative/runs/{run_id}/steps", headers=_hdr(yard_id=yard.id)
    )
    assert steps.status_code == 200
    tool_steps = [s for s in steps.json()["data"]["steps"] if s["step_type"] == "tool_call"]
    assert tool_steps and tool_steps[0]["tool_code"] == "web.search"
    assert tool_steps[0]["status"] == "ok"


def test_list_runs_summary_shape(api_client, session_factory, yard):
    _, _ = _completed_run(api_client, yard)
    listed = api_client.get("/api/v1/agents/factory/runs", headers=_hdr(yard_id=yard.id))
    assert listed.status_code == 200
    runs = listed.json()["data"]["runs"]
    assert len(runs) == 1
    row = runs[0]
    assert "blueprint_json" not in row
    assert row["status"] == "completed"
    assert 3 <= row["role_count"] <= 6
    assert row["needs_knowledge_count"] == row["role_count"]
    assert row["research_used"] is False
    assert row["research_sources"] == 0
    assert row["industry_brief"] == BRIEF


def test_failure_marks_run_failed_honestly(api_client, session_factory, yard, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("装配炸弹")

    monkeypatch.setattr(factory_pipeline.declarative, "create_agent", boom)
    resp = _generate(api_client, yard)
    ids = resp.json()["data"]
    task_row = _run_worker(ids["task_id"])

    # worker 侧如实 failed；run 侧 failed + error，不留半成品草稿
    assert task_row.status == "failed"
    db = worker_tasks._get_session_factory()()
    try:
        run_row = db.get(FactoryRun, ids["run_id"])
        assert run_row.status == "failed"
        assert "装配炸弹" in (run_row.error or "")
        assert db.query(YardAgent).filter(YardAgent.yard_id == yard.id).count() == 0
    finally:
        db.close()

    events = api_client.get(f"/api/v1/tasks/{ids['task_id']}/events", headers=_hdr(yard_id=yard.id))
    milestones = [
        e["payload_json"]["milestone"]
        for e in events.json()["data"]
        if e["event_type"] == "milestone"
    ]
    assert milestones[-1] == "failed"
