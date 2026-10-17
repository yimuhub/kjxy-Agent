"""任务语义集成测试（规范 §7.5）：取消安全点、投递失败标记与 retry 扩展。

- 取消安全点一：处理器执行前发现 meta.cancel_requested → 置 cancelled 并发事件。
- 取消安全点二：处理器执行中收到取消 → 保留结果、发事件明确标注，不再静默。
- 投递失败：enqueue 失败且无同步兜底 → meta.enqueue_failed=True + 事件。
- retry：接受 enqueue_failed 的 queued；拒绝普通 queued。

复用 test_tasks_api 的挂法：直接挂 tasks router 到独立 app，worker 会话工厂
注入同一内存库。
"""

import os

os.environ.setdefault("TASK_ENQUEUE_TIMEOUT_SECONDS", "0.5")

import pytest  # noqa: E402
from app.core.deps import get_db_session  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.main import create_app  # noqa: E402
from app.modules.tasks import (
    handlers,  # noqa: E402,F401 - 确保 mock.echo 已注册
    worker_tasks,  # noqa: E402
)
from app.modules.tasks.models import Task  # noqa: E402
from app.modules.tasks.router import router as tasks_router  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

TENANT_1 = "tenant-sem-1"
USER_1 = "user-sem-1"


def _token() -> str:
    return create_access_token(user_id=USER_1, tenant_id=TENANT_1, roles=["operator"])


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=eng)
    yield eng
    Base.metadata.drop_all(bind=eng)


@pytest.fixture()
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


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
    app.include_router(tasks_router, prefix="/api/v1")
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture()
def auth_headers():
    return {"Authorization": f"Bearer {_token()}"}


@pytest.fixture(autouse=True)
def patch_worker_session(session_factory):
    worker_tasks._session_factory = session_factory
    yield
    worker_tasks._session_factory = None


def _create(api_client, headers, task_type="mock.echo", input=None, idem_key=None):
    headers = dict(headers)
    if idem_key:
        headers["Idempotency-Key"] = idem_key
    return api_client.post(
        "/api/v1/tasks",
        json={"task_type": task_type, "input": input or {"question": "ping"}},
        headers=headers,
    )


def _set_meta(session_factory, task_id: str, **flags) -> None:
    """直接给任务 input_json.meta 打标（模拟 API 取消/投递失败等旁路写入）。"""
    session = session_factory()
    try:
        task = session.get(Task, task_id)
        meta = dict((task.input_json or {}).get("meta") or {})
        meta.update(flags)
        task.input_json = {**(task.input_json or {}), "meta": meta}
        session.commit()
    finally:
        session.close()


def _detail(api_client, headers, task_id):
    return api_client.get(f"/api/v1/tasks/{task_id}", headers=headers).json()["data"]


def _events(api_client, headers, task_id):
    body = api_client.get(f"/api/v1/tasks/{task_id}/events", headers=headers).json()
    return body["data"]


def test_cancel_takes_effect_before_handler_runs(api_client, auth_headers, session_factory):
    """安全点一：执行前发现取消请求 → cancelled，事件说明未执行处理器，结果不落库。"""
    created = _create(api_client, auth_headers, idem_key="sem-cancel-before").json()["data"]
    _set_meta(session_factory, created["id"], cancel_requested=True)

    worker_tasks.execute_task(created["id"])

    detail = _detail(api_client, auth_headers, created["id"])
    assert detail["status"] == "cancelled"
    assert detail["result_json"] is None
    events = _events(api_client, auth_headers, created["id"])
    messages = [e["message"] for e in events]
    assert any("取消在安全点生效，未执行处理器" in m for m in messages)
    assert not any("任务执行成功" in m for m in messages)


def test_cancel_during_execution_keeps_result_and_emits_event(
    api_client, auth_headers, session_factory
):
    """安全点二：处理器执行中收到取消 → 保留结果 + 事件明确标注副作用，不再静默。"""

    def _midway_cancel_handler(data, context):
        # 模拟处理器执行期间外部写入取消请求（例如 API 侧 cancel）
        session = worker_tasks._session_factory()
        try:
            task = session.get(Task, context["task_id"])
            meta = dict((task.input_json or {}).get("meta") or {})
            meta["cancel_requested"] = True
            task.input_json = {**(task.input_json or {}), "meta": meta}
            session.commit()
        finally:
            session.close()
        return {"done": True}

    handlers.TASK_HANDLERS["test.cancel_midway"] = _midway_cancel_handler
    try:
        created = _create(
            api_client, auth_headers, task_type="test.cancel_midway", idem_key="sem-cancel-during"
        ).json()["data"]

        worker_tasks.execute_task(created["id"])

        detail = _detail(api_client, auth_headers, created["id"])
        assert detail["status"] == "cancelled"
        assert detail["result_json"] == {"done": True}
        events = _events(api_client, auth_headers, created["id"])
        messages = [e["message"] for e in events]
        assert any("任务完成前收到取消请求，外部副作用可能已发生" in m for m in messages)
    finally:
        handlers.TASK_HANDLERS.pop("test.cancel_midway", None)


def test_worker_marks_alive_when_claiming_task(api_client, auth_headers, monkeypatch):
    """worker 领取任务时刷新心跳（app.workers.heartbeat.mark_worker_alive）。"""
    calls: list[int] = []
    import app.workers.heartbeat as heartbeat_mod

    monkeypatch.setattr(heartbeat_mod, "mark_worker_alive", lambda: calls.append(1))

    created = _create(api_client, auth_headers, idem_key="sem-heartbeat").json()["data"]
    worker_tasks.execute_task(created["id"])
    assert calls  # 至少刷新了一次
    assert _detail(api_client, auth_headers, created["id"])["status"] == "succeeded"


def test_enqueue_failure_marks_meta_and_event(api_client, auth_headers, monkeypatch):
    """broker 投递失败且无同步兜底：meta.enqueue_failed=True + enqueue_failed 事件。"""
    monkeypatch.setattr("app.modules.tasks.router.enqueue_task", lambda task_id: False)

    created = _create(api_client, auth_headers, idem_key="sem-enqueue-fail").json()["data"]
    assert created["input_json"]["meta"]["enqueue_failed"] is True

    detail = _detail(api_client, auth_headers, created["id"])
    assert detail["status"] == "queued"
    assert detail["input_json"]["meta"]["enqueue_failed"] is True
    events = _events(api_client, auth_headers, created["id"])
    assert any(e["event_type"] == "enqueue_failed" for e in events)


def test_retry_accepts_queued_with_enqueue_failed(api_client, auth_headers, monkeypatch):
    """retry 语义扩展：queued 且 meta.enqueue_failed=True 可重试（原任务保持 queued）。"""
    monkeypatch.setattr("app.modules.tasks.router.enqueue_task", lambda task_id: False)
    created = _create(api_client, auth_headers, idem_key="sem-retry-flag").json()["data"]

    # 放行 retry 后的重新投递，验证新任务正常入队
    monkeypatch.setattr("app.modules.tasks.router.enqueue_task", lambda task_id: True)
    resp = api_client.post(f"/api/v1/tasks/{created['id']}/retry", headers=auth_headers)
    assert resp.status_code == 201, resp.text
    new_task = resp.json()["data"]
    assert new_task["id"] != created["id"]
    assert new_task["status"] == "queued"
    assert new_task["input_json"]["meta"]["retry_of"] == created["id"]
    assert new_task["input_json"]["data"] == {"question": "ping"}

    original = _detail(api_client, auth_headers, created["id"])
    assert original["status"] == "queued"


def test_retry_rejects_plain_queued(api_client, auth_headers):
    """未标记投递失败的普通 queued 任务仍不可重试。"""
    created = _create(api_client, auth_headers, idem_key="sem-retry-plain").json()["data"]
    resp = api_client.post(f"/api/v1/tasks/{created['id']}/retry", headers=auth_headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "TASK_NOT_RETRYABLE"
