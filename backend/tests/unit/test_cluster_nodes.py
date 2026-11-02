"""多集群节点：登记、权限、URL 校验与版本探测。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.deps import (
    CurrentUser,
    YardContext,
    get_current_user,
    get_db_session,
    get_yard_context,
)
from app.core.errors import (
    AppError,
    app_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.core.middleware import RequestIDMiddleware
from app.modules.cluster import service
from app.modules.cluster.router import router as cluster_router
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

VERSION = "3.48.0"
HEAD = "0064_cluster_nodes"


def _ctx(roles: list[str] | None = None, user_id: str = "u-platform") -> YardContext:
    return YardContext(
        yard_id="yard-p5",
        user_id=user_id,
        roles=roles if roles is not None else ["platform_admin"],
        request_id="req-p5",
    )


def _client(db, ctx: YardContext) -> TestClient:  # type: ignore[no-untyped-def]
    app = FastAPI(version=VERSION)
    app.add_middleware(RequestIDMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
    app.include_router(cluster_router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_yard_context] = lambda: ctx
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=ctx.user_id, yard_id=ctx.yard_id, roles=list(ctx.roles), request_id=ctx.request_id
    )
    return TestClient(app)


def _fake_meta(version: str = VERSION, head: str = HEAD):
    def fetch(_base_url: str) -> tuple[dict[str, str], int]:
        return {"version": version, "migration_head": head}, 42

    return fetch


def test_cluster_node_crud_and_url_validation(db_session):
    admin = _client(db_session, _ctx())
    assert admin.get("/api/v1/cluster/nodes").status_code == 200

    created = admin.post(
        "/api/v1/cluster/nodes",
        json={"name": "华东节点", "base_url": "https://east.example.com/", "note": "只读体检"},
    )
    assert created.status_code == 200
    node = created.json()["data"]["node"]
    assert node["base_url"] == "https://east.example.com"
    assert node["status"] == "never"
    assert node["status_label"] == "未探测"

    duplicate = admin.post(
        "/api/v1/cluster/nodes",
        json={"name": "华东节点", "base_url": "https://other.example.com"},
    )
    assert duplicate.status_code == 409

    for bad in (
        "ftp://example.com",
        "https://user:pass@example.com",
        "https://example.com/?x=1",
        "example.com",
    ):
        resp = admin.post(
            "/api/v1/cluster/nodes",
            json={"name": f"坏地址 {bad}", "base_url": bad},
        )
        assert resp.status_code == 400, bad

    updated = admin.put(
        f"/api/v1/cluster/nodes/{node['id']}",
        json={"name": "华东主节点", "base_url": "https://east2.example.com"},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["node"]["name"] == "华东主节点"
    assert updated.json()["data"]["node"]["base_url"] == "https://east2.example.com"

    listed = admin.get("/api/v1/cluster/nodes")
    assert listed.json()["data"]["summary"]["total"] == 1
    assert listed.json()["data"]["summary"]["never"] == 1

    deleted = admin.delete(f"/api/v1/cluster/nodes/{node['id']}")
    assert deleted.status_code == 200
    assert admin.get("/api/v1/cluster/nodes").json()["data"]["summary"]["total"] == 0
    assert admin.delete(f"/api/v1/cluster/nodes/{node['id']}").status_code == 404


def test_probe_online_degraded_offline_and_stale(db_session, monkeypatch):
    admin = _client(db_session, _ctx())
    monkeypatch.setattr(service, "migration_head", lambda _db: HEAD)
    node = admin.post(
        "/api/v1/cluster/nodes",
        json={"name": "节点 A", "base_url": "https://a.example.com"},
    ).json()["data"]["node"]

    monkeypatch.setattr(service, "_fetch_meta", _fake_meta())
    online = admin.post(f"/api/v1/cluster/nodes/{node['id']}/probe")
    assert online.status_code == 200
    body = online.json()["data"]["node"]
    assert body["status"] == "online"
    assert body["latency_ms"] == 42
    assert body["version"] == VERSION
    assert body["migration_head"] == HEAD
    assert body["stale"] is False

    monkeypatch.setattr(service, "_fetch_meta", _fake_meta(version="3.47.0"))
    degraded = admin.post(f"/api/v1/cluster/nodes/{node['id']}/probe")
    assert degraded.json()["data"]["node"]["status"] == "degraded"
    assert "版本" in degraded.json()["data"]["node"]["last_error"]

    def boom(_base_url: str):
        raise TimeoutError("connect timeout")

    monkeypatch.setattr(service, "_fetch_meta", boom)
    offline = admin.post(f"/api/v1/cluster/nodes/{node['id']}/probe")
    assert offline.json()["data"]["node"]["status"] == "offline"
    assert offline.json()["data"]["node"]["latency_ms"] == 0

    row = service.get_node(db_session, node["id"])
    row.last_checked_at = datetime.now(UTC) - timedelta(seconds=service.STALE_SECONDS + 5)
    db_session.commit()
    listed = admin.get("/api/v1/cluster/nodes").json()["data"]["items"][0]
    assert listed["stale"] is True


def test_probe_all_summary_and_permissions(db_session, monkeypatch):
    yard_admin = _client(db_session, _ctx(roles=["yard_admin"]))
    assert yard_admin.get("/api/v1/cluster/nodes").status_code == 403
    assert (
        yard_admin.post(
            "/api/v1/cluster/nodes",
            json={"name": "x", "base_url": "https://x.example.com"},
        ).status_code
        == 403
    )

    admin = _client(db_session, _ctx())
    monkeypatch.setattr(service, "migration_head", lambda _db: HEAD)
    for index in range(2):
        admin.post(
            "/api/v1/cluster/nodes",
            json={"name": f"节点 {index}", "base_url": f"https://node{index}.example.com"},
        )
    monkeypatch.setattr(service, "_fetch_meta", _fake_meta())
    probed = admin.post("/api/v1/cluster/probe-all")
    assert probed.status_code == 200
    data = probed.json()["data"]
    assert data["summary"] == {
        "total": 2,
        "online": 2,
        "degraded": 0,
        "offline": 0,
        "never": 0,
    }
    raw = probed.text.lower()
    assert "password" not in raw
    assert "token" not in raw
