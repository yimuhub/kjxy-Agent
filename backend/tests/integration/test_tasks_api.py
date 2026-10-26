"""任务 API 集成测试：创建 → 查询 → 事件 → 执行 → 取消 → 重试全流程。

- 直接挂载 app.modules.tasks.router（prefix=/api/v1）；v1 总路由的接入由技术负责人集成。
- 用内存 SQLite；worker 的会话工厂被覆盖为同一测试库。
- 用 "mock.echo" handler 做端到端执行验证。
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
from app.modules.tasks import state_machine as sm  # noqa: E402
from app.modules.tasks.router import router as tasks_router  # noqa: E402
from app.modules.tasks.service import TaskService  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

YARD_1 = "yard-1"
YARD_2 = "yard-2"
USER_1 = "user-1"


def _token(user_id: str, yard_id: str) -> str:
    return create_access_token(user_id=user_id, yard_id=yard_id, roles=["operator"])


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
    return {"Authorization": f"Bearer {_token(USER_1, YARD_1)}"}


@pytest.fixture()
def other_yard_headers():
    return {"Authorization": f"Bearer {_token('user-2', YARD_2)}"}


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
        json={"task_type": task_type, "input": input or {"question": "水稻分蘖期观察什么？"}},
        headers=headers,
    )


def test_create_task_returns_201(auth_headers, api_client):
    resp = _create(api_client, auth_headers, idem_key="key-create-1")
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["status"] == "queued"
    assert data["task_type"] == "mock.echo"
    assert data["id"]
    assert "request_id" in resp.json()


def test_idempotency_key_returns_same_task(auth_headers, api_client):
    first = _create(api_client, auth_headers, idem_key="key-idem-1")
    second = _create(api_client, auth_headers, idem_key="key-idem-1")
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["data"]["id"] == second.json()["data"]["id"]


def test_idempotency_key_is_yard_scoped(auth_headers, other_yard_headers, api_client):
    first = _create(api_client, auth_headers, idem_key="key-shared")
    second = _create(api_client, other_yard_headers, idem_key="key-shared")
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["data"]["id"] != second.json()["data"]["id"]


def test_list_tasks_with_status_filter(auth_headers, api_client):
    _create(api_client, auth_headers, idem_key="key-list-1")
    resp = api_client.get("/api/v1/tasks", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["total"] >= 1
    assert body["meta"]["page"] == 1

    filtered = api_client.get("/api/v1/tasks?status=queued", headers=auth_headers)
    assert filtered.status_code == 200
    assert all(t["status"] == "queued" for t in filtered.json()["data"])

    empty = api_client.get("/api/v1/tasks?status=succeeded", headers=auth_headers)
    assert empty.json()["meta"]["total"] == 0


def test_get_task_and_events(auth_headers, api_client):
    created = _create(api_client, auth_headers, idem_key="key-detail-1").json()["data"]
    detail = api_client.get(f"/api/v1/tasks/{created['id']}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["data"]["id"] == created["id"]

    events = api_client.get(f"/api/v1/tasks/{created['id']}/events", headers=auth_headers)
    assert events.status_code == 200
    items = events.json()["data"]
    assert len(items) >= 1
    assert items[0]["event_type"] == "created"


def test_cross_yard_task_is_invisible(auth_headers, other_yard_headers, api_client):
    created = _create(api_client, auth_headers, idem_key="key-xyard-1").json()["data"]
    task_id = created["id"]

    resp = api_client.get(f"/api/v1/tasks/{task_id}", headers=other_yard_headers)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "TASK_NOT_FOUND"

    events = api_client.get(f"/api/v1/tasks/{task_id}/events", headers=other_yard_headers)
    assert events.status_code == 404

    listing = api_client.get("/api/v1/tasks", headers=other_yard_headers)
    assert listing.json()["meta"]["total"] == 0


def test_worker_executes_mock_echo_end_to_end(auth_headers, api_client):
    created = _create(
        api_client, auth_headers, idem_key="key-exec-1", input={"question": "ping"}
    ).json()["data"]
    task_id = created["id"]

    worker_tasks.execute_task(task_id)

    detail = api_client.get(f"/api/v1/tasks/{task_id}", headers=auth_headers).json()["data"]
    assert detail["status"] == "succeeded"
    assert detail["result_json"]["echo"] == {"question": "ping"}
    assert detail["started_at"] is not None
    assert detail["finished_at"] is not None

    events = api_client.get(f"/api/v1/tasks/{task_id}/events", headers=auth_headers).json()["data"]
    event_types = [e["event_type"] for e in events]
    assert "created" in event_types
    assert event_types.count("status_changed") >= 2


def test_worker_marks_unknown_handler_failed(auth_headers, api_client):
    created = _create(api_client, auth_headers, task_type="no.such.handler").json()["data"]
    worker_tasks.execute_task(created["id"])
    detail = api_client.get(f"/api/v1/tasks/{created['id']}", headers=auth_headers).json()["data"]
    assert detail["status"] == "failed"
    assert detail["error_code"] == "INVALID_REQUEST"


def test_cancel_queued_task(auth_headers, api_client):
    created = _create(api_client, auth_headers, idem_key="key-cancel-1").json()["data"]
    resp = api_client.post(f"/api/v1/tasks/{created['id']}/cancel", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "cancelled"

    again = api_client.post(f"/api/v1/tasks/{created['id']}/cancel", headers=auth_headers)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "TASK_NOT_CANCELLABLE"


def test_cancel_running_task_marks_cancel_requested(auth_headers, api_client, session_factory):
    from app.core.deps import YardContext

    created = _create(api_client, auth_headers, idem_key="key-cancel-2").json()["data"]
    ctx = YardContext(yard_id=YARD_1, user_id=USER_1, roles=[], request_id="req-test")
    session = session_factory()
    try:
        task = TaskService.get(session, ctx, created["id"])
        TaskService.set_status(session, task, sm.STATUS_RUNNING)
        session.commit()
    finally:
        session.close()

    resp = api_client.post(f"/api/v1/tasks/{created['id']}/cancel", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "cancelled"
    assert data["input_json"]["meta"]["cancel_requested"] is True


def test_retry_failed_task_creates_new_record(auth_headers, api_client):
    created = _create(
        api_client, auth_headers, task_type="no.such.handler", input={"question": "retry me"}
    ).json()["data"]
    worker_tasks.execute_task(created["id"])

    resp = api_client.post(f"/api/v1/tasks/{created['id']}/retry", headers=auth_headers)
    assert resp.status_code == 201
    new_task = resp.json()["data"]
    assert new_task["id"] != created["id"]
    assert new_task["status"] == "queued"
    assert new_task["task_type"] == "no.such.handler"
    assert new_task["input_json"]["data"] == {"question": "retry me"}
    assert new_task["input_json"]["meta"]["retry_of"] == created["id"]


def test_retry_non_retryable_task_conflicts(auth_headers, api_client):
    created = _create(api_client, auth_headers, idem_key="key-retry-409").json()["data"]
    resp = api_client.post(f"/api/v1/tasks/{created['id']}/retry", headers=auth_headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "TASK_NOT_RETRYABLE"


def test_unauthorized_is_rejected(api_client):
    resp = api_client.post("/api/v1/tasks", json={"task_type": "mock.echo", "input": {}})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


def test_create_task_validation_error(auth_headers, api_client):
    resp = api_client.post("/api/v1/tasks", json={"input": {}}, headers=auth_headers)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_REQUEST"
