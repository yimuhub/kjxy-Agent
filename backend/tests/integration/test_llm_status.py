"""观测面出入口径（S-5）：/llm/status 需登录，/metrics 默认关闭。"""

from app.core.config import settings
from app.core.deps import CurrentUser, get_current_user
from app.core.errors import AppError, app_error_handler
from app.modules.llm.router import router as llm_router
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app(*, logged_in: bool = True) -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(llm_router, prefix="/api/v1")
    if logged_in:
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id="u-status", yard_id="yard-1", roles=["viewer"], request_id="req-status"
        )
    return app


def test_llm_status_ok():
    client = TestClient(_make_app())
    resp = client.get("/api/v1/llm/status")
    assert resp.status_code == 200
    body = resp.json()
    data = body["data"]
    assert data["provider"] == "fake"  # conftest 固定 LLM_PROVIDER=fake
    assert data["configured"] is True
    assert data["model"]
    assert data["mode"] == "mock"
    assert body["request_id"]


def test_llm_status_requires_login():
    """未登录不能读出供应商/模型/是否配好密钥——那等于把部署形态交给外部探测。"""
    client = TestClient(_make_app(logged_in=False))
    resp = client.get("/api/v1/llm/status")
    assert resp.status_code == 401


def test_llm_status_leaks_no_secrets():
    client = TestClient(_make_app())
    text = client.get("/api/v1/llm/status").text.lower()
    for leaked in ("api_key", "apikey", "secret", "bearer", "sk-", "password"):
        assert leaked not in text, f"响应疑似泄露敏感信息：{leaked}"


def test_metrics_off_by_default():
    assert type(settings).model_fields["metrics_enabled"].default is False


def test_metrics_hidden_when_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "metrics_enabled", False)
    assert client.get("/metrics").status_code == 404


def test_metrics_exposed_when_explicitly_enabled(client, monkeypatch):
    monkeypatch.setattr(settings, "metrics_enabled", True)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
