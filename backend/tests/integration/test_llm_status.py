"""GET /llm/status 集成测试：状态可读且不泄露密钥。"""

from app.core.errors import AppError, app_error_handler
from app.modules.llm.router import router as llm_router
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(llm_router, prefix="/api/v1")
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


def test_llm_status_leaks_no_secrets():
    client = TestClient(_make_app())
    text = client.get("/api/v1/llm/status").text.lower()
    for leaked in ("api_key", "apikey", "secret", "bearer", "sk-", "password"):
        assert leaked not in text, f"响应疑似泄露敏感信息：{leaked}"
