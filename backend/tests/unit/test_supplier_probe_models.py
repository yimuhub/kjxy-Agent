"""供应商模型列表探测与本地模型放行测试（v1.3 §A + §B）。

全离线：httpx.MockTransport 假扮上游。
"""

import httpx
import pytest
from app.core.security import create_access_token
from app.modules.llm import supplier_service
from app.modules.tenants.repository import create_tenant

PROBE = "/api/v1/llm/providers/probe-models"


def _hdr(tenant_id: str | None = None, roles=("tenant_admin",)):
    token = create_access_token(user_id="u-probe", tenant_id=tenant_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def tenant(db_session):
    t = create_tenant(db_session, name="探测租户")
    db_session.commit()
    return t


@pytest.fixture(autouse=True)
def _clean_transport():
    supplier_service.set_test_transport(None)
    yield
    supplier_service.set_test_transport(None)


def _mock_models(ids: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": i} for i in ids]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_probe_models_returns_remote_list(monkeypatch, client, tenant):
    supplier_service.set_test_transport(_mock_models(["qwen2.5:7b", "llama3.1:8b"]))
    resp = client.post(
        PROBE,
        json={"provider_type": "custom", "base_url": "http://127.0.0.1:11434/v1"},
        headers=_hdr(tenant_id=tenant.id),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["source"] == "remote"
    assert [m["id"] for m in data["models"]] == ["qwen2.5:7b", "llama3.1:8b"]


async def test_probe_models_localhost_allowed(monkeypatch, client, tenant):
    """本地推理（127.0.0.1）必须放行——v1.3 修复的核心。"""
    supplier_service.set_test_transport(_mock_models(["local-model"]))
    resp = client.post(
        PROBE,
        json={"provider_type": "custom", "base_url": "http://127.0.0.1:11434/v1"},
        headers=_hdr(tenant_id=tenant.id),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["source"] == "remote"


async def test_probe_models_metadata_address_still_blocked(client, tenant):
    """云元数据地址永远封死（任何开关都不放行）。"""
    resp = client.post(
        PROBE,
        json={"provider_type": "custom", "base_url": "http://169.254.169.254/latest/meta-data"},
        headers=_hdr(tenant_id=tenant.id),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["source"] == "error"
    assert "元数据" in data["message"] or "保留" in data["message"]


async def test_probe_models_unsupported_provider_no_egress(client, tenant):
    """anthropic 无统一列表端点：不发外呼，诚实说明。"""
    resp = client.post(
        PROBE,
        json={"provider_type": "anthropic", "base_url": "https://api.anthropic.com"},
        headers=_hdr(tenant_id=tenant.id),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["source"] == "unsupported"
    assert data["models"] == []


async def test_probe_models_upstream_error_honest(client, tenant):
    supplier_service.set_test_transport(httpx.MockTransport(lambda r: httpx.Response(401)))
    resp = client.post(
        PROBE,
        json={"provider_type": "openai", "base_url": "https://api.openai.com/v1"},
        headers=_hdr(tenant_id=tenant.id),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["source"] == "error"
    assert "401" in data["message"]


async def test_probe_models_requires_admin(client, tenant):
    resp = client.post(
        PROBE,
        json={"provider_type": "openai", "base_url": "https://api.openai.com/v1"},
        headers=_hdr(roles=("operator",), tenant_id=tenant.id),
    )
    assert resp.status_code == 403
