"""多集群节点：登记、权限、URL 校验与版本探测。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
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
from app.modules.auth.models import User
from app.modules.cluster import service
from app.modules.cluster.models import STATUS_ONLINE, ClusterNode
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


def _admin_user(
    db,  # type: ignore[no-untyped-def]
    *,
    user_id: str = "u-platform",
    username: str = "admin",
    status: str = "active",
) -> User:
    """平台管理员闸门现在会回查数据库，测试必须给真实用户行。"""
    row = User(
        id=user_id,
        username=username,
        password_hash="x",
        display_name="平台管理员",
        status=status,
    )
    db.add(row)
    db.flush()
    return row


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
    _admin_user(db_session)
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
    _admin_user(db_session)
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
    _admin_user(db_session)
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


def test_probe_blocks_cloud_metadata_target(db_session, monkeypatch):
    """探测出网必须过 net_guard：元数据地址在发请求前就被拦下。"""
    monkeypatch.setattr(service, "migration_head", lambda _db: HEAD)
    with pytest.raises(AppError) as exc:
        service._fetch_meta("http://169.254.169.254")
    assert exc.value.code == "SSRF_BLOCKED"
    assert "元数据" in exc.value.message

    with pytest.raises(AppError) as exc6:
        service._fetch_meta("http://[fd00:ec2::254]")
    assert exc6.value.code == "SSRF_BLOCKED"

    _admin_user(db_session)
    admin = _client(db_session, _ctx())
    node = admin.post(
        "/api/v1/cluster/nodes",
        json={"name": "元数据探针", "base_url": "http://169.254.169.254"},
    ).json()["data"]["node"]
    probed = admin.post(f"/api/v1/cluster/nodes/{node['id']}/probe")
    body = probed.json()["data"]["node"]
    assert probed.status_code == 200
    assert body["status"] == "offline"
    assert "禁止访问元数据" in body["last_error"]


def test_offline_clears_previous_version(db_session, monkeypatch):
    _admin_user(db_session)
    admin = _client(db_session, _ctx())
    node = admin.post(
        "/api/v1/cluster/nodes",
        json={"name": "节点 B", "base_url": "https://b.example.com"},
    ).json()["data"]["node"]

    monkeypatch.setattr(service, "_fetch_meta", _fake_meta())
    online = admin.post(f"/api/v1/cluster/nodes/{node['id']}/probe").json()["data"]["node"]
    assert online["version"] == VERSION
    assert online["last_ok_at"] is not None

    def boom(_base_url: str):
        raise TimeoutError("connect timeout")

    monkeypatch.setattr(service, "_fetch_meta", boom)
    offline = admin.post(f"/api/v1/cluster/nodes/{node['id']}/probe").json()["data"]["node"]
    assert offline["status"] == "offline"
    # 离线后不保留旧版本号，避免界面把过期版本当现况；上次成功时间仍可查
    assert offline["version"] == ""
    assert offline["migration_head"] == ""
    assert offline["last_ok_at"] == online["last_ok_at"]


def test_list_is_not_truncated_at_max_nodes(db_session):
    """并发顶破上限时，多出来的节点仍要出现在列表里（不再被 limit 裁掉）。"""
    from app.modules.cluster.models import STATUS_NEVER

    for index in range(service.MAX_NODES + 2):
        db_session.add(
            ClusterNode(
                name=f"溢出节点 {index}",
                base_url=f"https://overflow{index}.example.com",
                note="",
                status=STATUS_NEVER,
            )
        )
    db_session.flush()
    assert len(service.list_nodes(db_session)) == service.MAX_NODES + 2


def test_stale_semantics(db_session):
    now = datetime.now(UTC)
    never = ClusterNode(
        name="从未探测", base_url="https://never.example.com", note="", status="never"
    )
    legacy = ClusterNode(
        name="旧数据", base_url="https://legacy.example.com", note="", status=STATUS_ONLINE
    )
    db_session.add_all([never, legacy])
    db_session.flush()
    assert service._is_stale(never, now) is False
    # 有在线结论却缺时间戳：按过期处理，不能把历史值当现况
    assert service._is_stale(legacy, now) is True


def test_platform_gate_rejects_missing_user(db_session):
    admin = _client(db_session, _ctx())
    assert admin.get("/api/v1/cluster/nodes").status_code == 401


def test_platform_gate_rejects_user_outside_admin_list(db_session):
    _admin_user(db_session, username="ops")
    admin = _client(db_session, _ctx())
    assert admin.get("/api/v1/cluster/nodes").status_code == 403


def test_platform_gate_rejects_disabled_user(db_session):
    _admin_user(db_session, status="disabled")
    admin = _client(db_session, _ctx())
    response = admin.get("/api/v1/cluster/nodes")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCOUNT_DISABLED"
