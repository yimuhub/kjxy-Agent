"""供应商模型列表探测与本地模型放行测试（v1.3 §A + §B）。

全离线：httpx.MockTransport 假扮上游。
"""

import httpx
import pytest
from app.core.security import create_access_token
from app.modules.llm import supplier_service
from app.modules.yards.repository import create_yard

PROBE = "/api/v1/llm/providers/probe-models"


def _hdr(yard_id: str | None = None, roles=("yard_admin",)):
    token = create_access_token(user_id="u-probe", yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def yard(db_session):
    t = create_yard(db_session, name="探测小院")
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


async def test_probe_models_returns_remote_list(monkeypatch, client, yard):
    supplier_service.set_test_transport(_mock_models(["qwen2.5:7b", "llama3.1:8b"]))
    resp = client.post(
        PROBE,
        json={"provider_type": "custom", "base_url": "http://127.0.0.1:11434/v1"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["source"] == "remote"
    assert [m["id"] for m in data["models"]] == ["qwen2.5:7b", "llama3.1:8b"]


async def test_probe_models_localhost_allowed(monkeypatch, client, yard):
    """本地推理（127.0.0.1）必须放行——v1.3 修复的核心。"""
    supplier_service.set_test_transport(_mock_models(["local-model"]))
    resp = client.post(
        PROBE,
        json={"provider_type": "custom", "base_url": "http://127.0.0.1:11434/v1"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["source"] == "remote"


async def test_probe_models_metadata_address_still_blocked(client, yard):
    """云元数据地址永远封死（任何开关都不放行）。"""
    resp = client.post(
        PROBE,
        json={"provider_type": "custom", "base_url": "http://169.254.169.254/latest/meta-data"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["source"] == "error"
    assert "元数据" in data["message"] or "保留" in data["message"]


async def test_probe_models_unsupported_provider_no_egress(client, yard):
    """anthropic 无统一列表端点：不发外呼，诚实说明。"""
    resp = client.post(
        PROBE,
        json={"provider_type": "anthropic", "base_url": "https://api.anthropic.com"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["source"] == "unsupported"
    assert data["models"] == []


async def test_probe_models_upstream_error_honest(client, yard):
    supplier_service.set_test_transport(httpx.MockTransport(lambda r: httpx.Response(401)))
    resp = client.post(
        PROBE,
        json={"provider_type": "openai", "base_url": "https://api.openai.com/v1"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["source"] == "error"
    assert "401" in data["message"]


async def test_probe_models_requires_admin(client, yard):
    resp = client.post(
        PROBE,
        json={"provider_type": "openai", "base_url": "https://api.openai.com/v1"},
        headers=_hdr(roles=("operator",), yard_id=yard.id),
    )
    assert resp.status_code == 403


@pytest.mark.parametrize("provider_type", ["dashscope_image", "openai_video"])
async def test_probe_models_visual_types_accepted(client, yard, provider_type):
    """图像/视频通道类型不能再因 Literal 校验直接 422（v3.36 回归）。"""
    resp = client.post(
        PROBE,
        json={"provider_type": provider_type, "base_url": "https://example.com/v1"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["source"] == "unsupported"


@pytest.mark.parametrize("provider_type", ["openai_images", "official_relay"])
async def test_probe_models_visual_and_official_types_probe_upstream(client, yard, provider_type):
    """图像/官方供应商类型与 OpenAI 兼容族一样走 /models 探测。"""
    supplier_service.set_test_transport(_mock_models(["gpt-image-1"]))
    resp = client.post(
        PROBE,
        json={"provider_type": provider_type, "base_url": "https://example.com/v1"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["source"] == "remote"
    assert [m["id"] for m in data["models"]] == ["gpt-image-1"]
