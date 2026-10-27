"""联网搜索 HTTP 集成测试：状态接口、测试接口权限与错误透传（不联网）。"""

import pytest
from app.core.deps import (
    CurrentUser,
    YardContext,
    get_current_user,
    get_db_session,
    get_yard_context,
)
from app.core.errors import AppError, app_error_handler
from app.modules.search import service
from app.modules.search.router import router
from fastapi import FastAPI
from fastapi.testclient import TestClient

YARD = "yard-001"


def _make_app(db_session) -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = lambda: db_session
    app.dependency_overrides[get_yard_context] = lambda: YardContext(
        yard_id=YARD, user_id="u-1", roles=["yard_admin"], request_id="req-test"
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="u-1", yard_id=YARD, roles=["yard_admin"], request_id="req-test"
    )
    return app


@pytest.fixture()
def allow_permission(monkeypatch):
    monkeypatch.setattr("app.modules.yards.authz.has_permission", lambda *a, **k: True)


@pytest.fixture()
def deny_permission(monkeypatch):
    monkeypatch.setattr("app.modules.yards.authz.has_permission", lambda *a, **k: False)


def test_status_merges_provider_and_quota(db_session, monkeypatch, allow_permission):
    async def fake_status(db, yard_id):
        return {
            "mode": "online",
            "provider": "searxng",
            "internet": True,
            "checked_at": "2026-10-25T00:00:00Z",
            "reason": "搜索源可用。",
        }

    monkeypatch.setattr(service, "network_status", fake_status)
    monkeypatch.setattr(service, "select_providers", lambda db, yard_id: ("searxng", []))
    monkeypatch.setattr(
        service,
        "quota_snapshot",
        lambda db, yard_id: {"used": 3, "limit": 500, "remaining": 497},
    )

    client = TestClient(_make_app(db_session))
    resp = client.get("/api/v1/search/status")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["mode"] == "online"
    assert data["internet"] is True
    assert data["supplier"] == "searxng"
    assert data["quota"] == {"used": 3, "limit": 500, "remaining": 497}


def test_test_endpoint_returns_provider_count_and_latency(
    db_session, monkeypatch, allow_permission
):
    async def fake_search(db, yard_id, query, count=5, lang="zh-CN"):
        return {
            "query": query,
            "provider": "tavily",
            "results": [
                {"title": "a", "url": "https://example.com/a", "snippet": "", "source": "t"}
            ],
            "fetched_at": "2026-10-25T00:00:00Z",
            "cached": False,
        }

    monkeypatch.setattr(service, "search_web", fake_search)
    client = TestClient(_make_app(db_session))
    resp = client.post("/api/v1/search/test", json={"query": "水稻", "count": 1})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["provider"] == "tavily"
    assert data["result_count"] == 1
    assert data["query"] == "水稻"
    assert isinstance(data["latency_ms"], int)


def test_test_endpoint_body_optional(db_session, monkeypatch, allow_permission):
    async def fake_search(db, yard_id, query, count=5, lang="zh-CN"):
        return {
            "query": query,
            "provider": "searxng",
            "results": [],
            "fetched_at": "2026-10-25T00:00:00Z",
            "cached": True,
        }

    monkeypatch.setattr(service, "search_web", fake_search)
    client = TestClient(_make_app(db_session))
    resp = client.post("/api/v1/search/test")
    assert resp.status_code == 200
    assert resp.json()["data"]["result_count"] == 0


def test_test_endpoint_requires_permission(db_session, deny_permission):
    client = TestClient(_make_app(db_session))
    resp = client.post("/api/v1/search/test", json={"query": "x"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_test_endpoint_passes_through_app_error(db_session, monkeypatch, allow_permission):
    async def fake_search(db, yard_id, query, count=5, lang="zh-CN"):
        raise AppError("SEARCH_PROVIDER_UNAVAILABLE", "所有搜索供应商均不可用。", http_status=503)

    monkeypatch.setattr(service, "search_web", fake_search)
    client = TestClient(_make_app(db_session))
    resp = client.post("/api/v1/search/test", json={"query": "x"})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "SEARCH_PROVIDER_UNAVAILABLE"
