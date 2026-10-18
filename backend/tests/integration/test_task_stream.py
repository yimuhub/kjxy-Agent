"""SSE 任务事件流集成测试（GET /tasks/{id}/events/stream）+ tasks_inline_mode。

覆盖：
- 实时追尾：创建任务后订阅，能看到 stream-init → task-event → task-status →
  stream-end 的完整帧序列（含执行期间的进度事件）。
- 已终态任务：重放全部历史事件后立即 stream-end，不挂连接。
- tasks_inline_mode：True 时投递跳过 Celery、直接本机线程执行到成功。
- 超时保护：MAX_STREAM_SECONDS 调小后未到终态也主动 stream-end(timeout)。
- 越权/未授权/未知任务：与 GET /tasks/{id}/events 的拒绝语义完全一致。

并发安全：测试库用文件型 SQLite（tmp_path），API 线程、执行线程、流轮询
各用独立连接，避免 StaticPool 单连接跨线程共享的假并发。
"""

import json
import os
import threading
import time

os.environ.setdefault("TASK_ENQUEUE_TIMEOUT_SECONDS", "0.5")

import pytest  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.deps import get_db_session  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.main import create_app  # noqa: E402
from app.modules.tasks import handlers, worker_tasks  # noqa: E402
from app.modules.tasks import stream as stream_mod
from app.modules.tasks.router import router as tasks_router  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

TENANT_1 = "tenant-sse-1"
TENANT_2 = "tenant-sse-2"
USER_1 = "user-sse-1"
USER_2 = "user-sse-2"

TERMINAL = {"succeeded", "failed", "cancelled"}


def _token(tenant_id: str = TENANT_1, user_id: str = USER_1) -> str:
    return create_access_token(user_id=user_id, tenant_id=tenant_id, roles=["operator"])


@pytest.fixture()
def session_factory(tmp_path):
    """文件型 SQLite：多线程各有独立连接，贴近真实并发。"""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'stream-test.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    yield factory
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


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


@pytest.fixture()
def other_tenant_headers():
    return {"Authorization": f"Bearer {_token(TENANT_2, USER_2)}"}


@pytest.fixture(autouse=True)
def patch_worker_session(session_factory):
    worker_tasks._session_factory = session_factory
    yield
    worker_tasks._session_factory = None


@pytest.fixture()
def no_celery(monkeypatch):
    """屏蔽真实 Celery 投递（默认返回成功，任务保持 queued 由测试手动执行）。"""
    monkeypatch.setattr("app.modules.tasks.router.enqueue_task", lambda task_id: True)


@pytest.fixture()
def slow_handler():
    """慢处理器：两阶段进度事件（各写一条 progress 事件并单独提交），便于流式追尾。"""

    def _slow(data, context):
        from app.modules.tasks.models import TaskEvent

        for pct in (30.0, 70.0):
            time.sleep(0.9)
            session = worker_tasks._session_factory()
            try:
                session.add(
                    TaskEvent(
                        tenant_id=context["tenant_id"],
                        task_id=context["task_id"],
                        event_type="progress",
                        message=f"处理中（{int(pct)}%）",
                        progress=pct,
                    )
                )
                session.commit()
            finally:
                session.close()
        return {"echo": "stream-done"}

    handlers.TASK_HANDLERS["test.stream_slow"] = _slow
    yield "test.stream_slow"
    handlers.TASK_HANDLERS.pop("test.stream_slow", None)


def _create(api_client, headers, task_type="mock.echo", input=None, idem_key=None):
    headers = dict(headers)
    if idem_key:
        headers["Idempotency-Key"] = idem_key
    return api_client.post(
        "/api/v1/tasks",
        json={"task_type": task_type, "input": input or {"question": "ping"}},
        headers=headers,
    )


def _detail(api_client, headers, task_id):
    return api_client.get(f"/api/v1/tasks/{task_id}", headers=headers).json()["data"]


def _events(api_client, headers, task_id):
    return api_client.get(f"/api/v1/tasks/{task_id}/events", headers=headers).json()["data"]


def _wait_terminal(api_client, headers, task_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = _detail(api_client, headers, task_id)
        if detail["status"] in TERMINAL:
            return detail
        time.sleep(0.1)
    pytest.fail("任务未在超时内进入终态")


def _read_sse(api_client, headers, task_id, *, deadline=30.0):
    """打开 SSE 流并解析到服务端收尾；返回 [(event 名, data dict), ...]，含响应头断言。"""
    frames: list[tuple[str, dict]] = []
    url = f"/api/v1/tasks/{task_id}/events/stream"
    with api_client.stream("GET", url, headers=headers) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        # 禁用缓存与反代缓冲的统一信封豁免端点，响应头必须到位
        assert resp.headers.get("cache-control") == "no-cache"
        assert resp.headers.get("x-accel-buffering") == "no"
        event_name: str | None = None
        data_lines: list[str] = []
        started = time.monotonic()
        for line in resp.iter_lines():
            if time.monotonic() - started > deadline:
                pytest.fail(f"SSE 流未在 {deadline}s 内收尾，已收帧：{frames}")
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip(" "))
            elif not line:
                if event_name or data_lines:
                    payload = json.loads("\n".join(data_lines)) if data_lines else {}
                    frames.append((event_name or "message", payload))
                event_name, data_lines = None, []
    return frames


def _names(frames):
    return [name for name, _ in frames]


def _payload_of(frames, name):
    return [data for n, data in frames if n == name]


# ---------------------------------------------------------------------------
# 实时追尾
# ---------------------------------------------------------------------------


def test_stream_tails_live_events_until_terminal(api_client, auth_headers, no_celery, slow_handler):
    """创建（queued）→ DelayedThread 执行 → 流中先收 stream-init，再增量收到
    进度事件与 running/succeeded 状态帧，终态后补发完剩余事件并 stream-end。"""
    created = _create(api_client, auth_headers, task_type=slow_handler, idem_key="sse-live").json()[
        "data"
    ]
    task_id = created["id"]

    # 0.3s 后执行：保证订阅首帧快照只含 created 事件，随后增量追尾
    thread = threading.Thread(
        target=lambda: (time.sleep(0.3), worker_tasks.run_task_inline(task_id)),
        name="sse-live-run",
        daemon=True,
    )
    thread.start()

    frames = _read_sse(api_client, auth_headers, task_id, deadline=25.0)
    names = _names(frames)

    assert names[0] == "stream-init"
    init = frames[0][1]
    assert init["status"] == "queued"
    assert init["event_count"] == 1  # 订阅瞬间只有 created 事件，其余为增量推送

    assert names[-1] == "stream-end"
    assert frames[-1][1]["status"] == "succeeded"
    assert frames[-1][1]["reason"] == "task_terminal"
    assert names.count("stream-end") == 1

    status_frames = _payload_of(frames, "task-status")
    assert [f["status"] for f in status_frames] == ["running", "succeeded"]
    assert status_frames[-1]["progress"] == 70.0  # 状态帧附带最近一次进度

    progress_messages = [
        f["message"] for f in _payload_of(frames, "task-event") if "处理中" in f["message"]
    ]
    assert progress_messages == ["处理中（30%）", "处理中（70%）"]

    # 流内收到的 task-event 与事后 REST 拉取的事件一一对应（不重不漏）
    rest_events = _events(api_client, auth_headers, task_id)
    stream_ids = [f["id"] for f in _payload_of(frames, "task-event")]
    assert stream_ids == [e["id"] for e in rest_events]


# ---------------------------------------------------------------------------
# 已终态：全量重放后立即收尾
# ---------------------------------------------------------------------------


def test_stream_replays_finished_task_then_ends(api_client, auth_headers, no_celery):
    created = _create(api_client, auth_headers, idem_key="sse-terminal").json()["data"]
    task_id = created["id"]
    worker_tasks.run_task_inline(task_id)  # 同步执行到 succeeded
    assert _detail(api_client, auth_headers, task_id)["status"] == "succeeded"

    started = time.monotonic()
    frames = _read_sse(api_client, auth_headers, task_id, deadline=10.0)
    names = _names(frames)

    assert names[0] == "stream-init"
    assert frames[0][1]["status"] == "succeeded"
    assert names[-1] == "stream-end"
    assert names.count("stream-end") == 1
    rest_events = _events(api_client, auth_headers, task_id)
    assert frames[0][1]["event_count"] == len(rest_events)
    assert _payload_of(frames, "task-event") == rest_events
    # 状态未再变化：不应出现 task-status 帧
    assert "task-status" not in names
    # 已终态不挂连接：首帧轮询间隔内即收尾
    assert time.monotonic() - started < 5.0


# ---------------------------------------------------------------------------
# tasks_inline_mode：跳过 Celery，直行本机线程
# ---------------------------------------------------------------------------


def test_inline_mode_skips_celery_and_runs_locally(api_client, auth_headers, monkeypatch):
    enqueue_calls: list[str] = []
    monkeypatch.setattr(
        "app.modules.tasks.router.enqueue_task", lambda task_id: enqueue_calls.append(task_id)
    )
    monkeypatch.setattr(settings, "tasks_inline_mode", True)

    created = _create(api_client, auth_headers, idem_key="sse-inline").json()["data"]
    assert enqueue_calls == []  # inline 模式不触碰 Celery

    detail = _wait_terminal(api_client, auth_headers, created["id"])
    assert detail["status"] == "succeeded"
    assert detail["result_json"]["echo"] == {"question": "ping"}
    # 收尾后订阅：可直接经 SSE 重放看到全过程（证明事件已由线程写全）
    frames = _read_sse(api_client, auth_headers, created["id"], deadline=10.0)
    assert _names(frames)[-1] == "stream-end"


# ---------------------------------------------------------------------------
# 超时保护：未到终态也主动收尾
# ---------------------------------------------------------------------------


def test_stream_timeout_emits_stream_end(api_client, auth_headers, no_celery, monkeypatch):
    monkeypatch.setattr(stream_mod, "MAX_STREAM_SECONDS", 0.5)
    monkeypatch.setattr(stream_mod, "POLL_INTERVAL_SECONDS", 0.1)

    created = _create(api_client, auth_headers, idem_key="sse-timeout").json()["data"]
    frames = _read_sse(api_client, auth_headers, created["id"], deadline=10.0)

    assert _names(frames)[0] == "stream-init"
    end = _payload_of(frames, "stream-end")
    assert len(end) == 1
    assert end[0]["reason"] == "timeout"
    assert end[0]["status"] == "queued"
    # 超时收尾后任务仍在排队：取消是显式动作，断流从不杀任务
    assert _detail(api_client, auth_headers, created["id"])["status"] == "queued"


# ---------------------------------------------------------------------------
# 权限：与 GET /tasks/{id}/events 完全一致
# ---------------------------------------------------------------------------


def test_stream_access_matches_events_endpoint(
    api_client, auth_headers, no_celery, other_tenant_headers
):
    created = _create(api_client, auth_headers, idem_key="sse-access").json()["data"]
    task_id = created["id"]

    # 跨租户：与 events 端点同样 404 TASK_NOT_FOUND（防枚举，而不是 403）
    for suffix in ("events", "events/stream"):
        resp = api_client.get(f"/api/v1/tasks/{task_id}/{suffix}", headers=other_tenant_headers)
        assert resp.status_code == 404, suffix
        assert resp.json()["error"]["code"] == "TASK_NOT_FOUND", suffix

    # 未知任务：同样 404
    resp = api_client.get("/api/v1/tasks/nonexistent-task/events/stream", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "TASK_NOT_FOUND"

    # 未授权：与 events 端点同样 401
    for suffix in ("events", "events/stream"):
        resp = api_client.get(f"/api/v1/tasks/{task_id}/{suffix}")
        assert resp.status_code == 401, suffix
