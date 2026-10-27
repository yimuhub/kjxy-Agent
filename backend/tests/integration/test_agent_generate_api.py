"""一句话生成智能体 API 集成测试（v3.5）：preview → generate 202 → 里程碑 → 草稿。

- 文件型 SQLite + 覆盖 worker 会话工厂（与 test_agent_factory_api 同一并发安全
  模式），worker 同步 run_task_inline。
- LLM 用 FakeProvider（确定性非 JSON 输出）→ 规格必然落保守兜底模板，草稿仍
  可用并带 note 提示人工复核——「模型不合作也不产垃圾」的口径。
- 里程碑断言读 /tasks/{id}/events（SSE 读的同一张 task_events 表），另有一条
  用例直接断言 SSE 帧序列。
"""

from types import SimpleNamespace

import pytest
from app.core.deps import get_db_session
from app.core.errors import AppError
from app.core.security import create_access_token
from app.db.base import Base
from app.main import create_app
from app.modules.agents import agent_builder
from app.modules.agents.models import YardAgent
from app.modules.audit.models import AuditLog
from app.modules.tasks import worker_tasks
from app.modules.tasks.models import Task
from app.modules.yards.repository import create_yard
from app.modules.yards.settings_service import update_setting
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

UTTERANCE = "帮我做一个病虫害防治智能体，能查知识库并给防治建议"


def _hdr(user_id="u-gen-1", yard_id=None, roles=("member",)):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'agent-gen-test.db'}",
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
        t = create_yard(db, name="一句话生成小院")
        db.commit()
        return SimpleNamespace(id=t.id)
    finally:
        db.close()


def _set_settings(session_factory, yard_id, **cfg):
    db = session_factory()
    try:
        update_setting(
            db,
            yard_id=yard_id,
            key="agent_generate",
            value=cfg,
            actor_id="u-gen-1",
            request_id="req",
        )
        db.commit()
    finally:
        db.close()


def _post_generate(api_client, yard, headers=None, **overrides):
    body = {"utterance": UTTERANCE, "visibility": "private", "use_web": False}
    body.update(overrides)
    return api_client.post(
        "/api/v1/agents/generate", json=body, headers=headers or _hdr(yard_id=yard.id)
    )


def _run_worker(task_id) -> Task:
    worker_tasks.run_task_inline(task_id)
    db = worker_tasks._get_session_factory()()
    try:
        return db.get(Task, task_id)
    finally:
        db.close()


def _milestones(api_client, yard, task_id) -> list[str]:
    resp = api_client.get(f"/api/v1/tasks/{task_id}/events", headers=_hdr(yard_id=yard.id))
    assert resp.status_code == 200
    return [
        e["payload_json"]["milestone"]
        for e in resp.json()["data"]
        if e["event_type"] == "milestone" and "milestone" in (e.get("payload_json") or {})
    ]


# ---------------------------------------------------------------------------
# 预览与策略
# ---------------------------------------------------------------------------


def test_preview_returns_policy_and_no_llm(api_client, yard):
    resp = api_client.post(
        "/api/v1/agents/generate/preview",
        json={"utterance": UTTERANCE},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["utterance"] == UTTERANCE
    assert data["policy"]["enabled"] is True and data["policy"]["limit"] == 10
    assert isinstance(data["matches"], list)


def test_policy_endpoint_reflects_settings(api_client, session_factory, yard):
    _set_settings(session_factory, yard.id, daily_limit=3)
    resp = api_client.get("/api/v1/agents/generate/policy", headers=_hdr(yard_id=yard.id))
    assert resp.status_code == 200
    policy = resp.json()["data"]
    assert policy["limit"] == 3 and policy["used"] == 0 and policy["remaining"] == 3


# ---------------------------------------------------------------------------
# 生成主链路
# ---------------------------------------------------------------------------


def test_generate_creates_draft_with_blueprint_and_milestones(api_client, session_factory, yard):
    resp = _post_generate(api_client, yard)
    assert resp.status_code == 202, resp.text
    task_id = resp.json()["data"]["task_id"]

    task = _run_worker(task_id)
    assert task.status == "succeeded", (task.status, task.error_message)

    # 草稿落库：created_from=generated、gen_ 前缀、blueprint + 待补清单
    db = worker_tasks._get_session_factory()()
    try:
        drafts = db.query(YardAgent).filter(YardAgent.yard_id == yard.id).all()
        assert len(drafts) == 1
        agent = drafts[0]
        assert agent.created_from == "generated" and agent.status == "draft"
        assert agent.agent_code.startswith("gen_")
        assert agent.blueprint_json["framework"] == "agent-framework/v3.5"
        assert agent.blueprint_json["note"], "FakeLLM 非 JSON → 必须带人工复核备注"
        assert isinstance(agent.skill_requirements, list)
        assert agent.created_by == "u-gen-1"
        actions = {
            row[0] for row in db.query(AuditLog.action).filter(AuditLog.yard_id == yard.id).all()
        }
        assert {"agent_generate_requested", "agent_generated"} <= actions
    finally:
        db.close()

    milestones = _milestones(api_client, yard, task_id)
    assert milestones == ["retrieving", "designing", "matching", "persisting", "completed"]


def test_sse_stream_frames_contain_milestones(api_client, yard):
    resp = _post_generate(api_client, yard)
    task_id = resp.json()["data"]["task_id"]
    _run_worker(task_id)
    with api_client.stream(
        "GET", f"/api/v1/tasks/{task_id}/events/stream", headers=_hdr(yard_id=yard.id)
    ) as stream:
        body = "".join(stream.iter_text())
    assert "event: stream-init" in body
    assert "event: task-event" in body
    assert "designing" in body and "persisting" in body
    assert "event: stream-end" in body


def test_task_result_summary_shape(api_client, yard):
    resp = _post_generate(api_client, yard)
    task_id = resp.json()["data"]["task_id"]
    _run_worker(task_id)
    detail = api_client.get(f"/api/v1/tasks/{task_id}", headers=_hdr(yard_id=yard.id))
    assert detail.status_code == 200
    data = detail.json()["data"]
    result = data.get("result_json") or {}
    assert result.get("agent_id")
    assert result.get("agent_code", "").startswith("gen_")
    assert isinstance(result.get("matched"), list)
    assert isinstance(result.get("skill_requirements"), list)


# ---------------------------------------------------------------------------
# 权限与配额
# ---------------------------------------------------------------------------


def test_member_requesting_public_forbidden(api_client, yard):
    resp = _post_generate(api_client, yard, visibility="public")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"
    # 私有正常
    ok_resp = _post_generate(api_client, yard, visibility="private")
    assert ok_resp.status_code == 202


def test_admin_can_request_public(api_client, yard):
    resp = _post_generate(
        api_client,
        yard,
        headers=_hdr(roles=("yard_admin",), yard_id=yard.id),
        visibility="public",
    )
    assert resp.status_code == 202


def test_disabled_setting_returns_403(api_client, session_factory, yard):
    _set_settings(session_factory, yard.id, enabled=False)
    resp = _post_generate(api_client, yard)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AGENT_GENERATE_DISABLED"


def test_daily_quota_per_user(api_client, session_factory, yard):
    _set_settings(session_factory, yard.id, daily_limit=1)
    first = _post_generate(api_client, yard)
    assert first.status_code == 202
    second = _post_generate(api_client, yard)
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "AGENT_GENERATE_QUOTA"
    # 另一个成员不受影响
    other = _post_generate(api_client, yard, headers=_hdr(user_id="u-gen-2", yard_id=yard.id))
    assert other.status_code == 202


def test_idempotency_same_utterance_same_task(api_client, yard):
    first = _post_generate(api_client, yard)
    second = _post_generate(api_client, yard)
    assert first.status_code == 202 and second.status_code == 202
    assert first.json()["data"]["task_id"] == second.json()["data"]["task_id"]


# ---------------------------------------------------------------------------
# 失败口径：模型完全不可用 → 502、不落草稿、里程碑 failed
# ---------------------------------------------------------------------------


def test_llm_unavailable_fails_task_without_draft(api_client, yard, monkeypatch):
    async def _boom(*args, **kwargs):
        raise AppError("LLM_ERROR", "供应商不可用", http_status=502)

    monkeypatch.setattr(agent_builder, "complete_with_logging", _boom)
    resp = _post_generate(api_client, yard)
    task_id = resp.json()["data"]["task_id"]
    task = _run_worker(task_id)
    assert task.status == "failed"
    milestones = _milestones(api_client, yard, task_id)
    assert milestones[-1] == "failed"
    db = worker_tasks._get_session_factory()()
    try:
        assert db.query(YardAgent).filter(YardAgent.yard_id == yard.id).count() == 0
    finally:
        db.close()


def test_short_utterance_rejected(api_client, yard):
    resp = api_client.post(
        "/api/v1/agents/generate",
        json={"utterance": "短的"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 202  # 入队侧不拦；处理器内 400 → task failed
    task = _run_worker(resp.json()["data"]["task_id"])
    assert task.status == "failed"
