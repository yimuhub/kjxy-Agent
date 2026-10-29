"""图像供应商协议适配与解析链测试（v3.26 D-1.4）。"""

from __future__ import annotations

import base64

import pytest
from app.core.errors import AppError
from app.modules.llm import images, supplier_service
from app.modules.llm.capabilities import CAPABILITY_CODES
from app.modules.llm.supplier_schemas import SupplierCreate

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16
JPG = b"\xff\xd8\xff\xe0" + b"0" * 16


class _Resp:
    def __init__(self, status: int = 200, payload=None, content: bytes = b"") -> None:
        self.status_code = status
        self._payload = payload
        self.content = content

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _Client:
    """按 ``.routes`` 分派的假 httpx.AsyncClient。"""

    routes: dict = {}
    calls: list = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        type(self).calls.append(("post", url, json or {}))
        return type(self).routes["post"](url, json or {})

    async def get(self, url, headers=None):
        type(self).calls.append(("get", url))
        return type(self).routes["get"](url)


@pytest.fixture()
def fake_httpx(monkeypatch):
    monkeypatch.setattr(images.httpx, "AsyncClient", _Client)
    _Client.calls = []
    _Client.routes = {}
    return _Client


def _openai(**kw):
    params = {
        "base_url": "https://img.example.com/v1",
        "api_key": "sk-test",
        "model": "gpt-image-1",
    }
    params.update(kw)
    return images.OpenAIImagesProvider(**params)


@pytest.mark.asyncio
async def test_openai_images_parses_b64(fake_httpx):
    fake_httpx.routes["post"] = lambda url, body: _Resp(
        200, {"data": [{"b64_json": base64.b64encode(PNG).decode()}]}
    )
    batch = await _openai().generate(images.ImageRequest(prompt="中秋脐橙礼盒海报", n=1))
    assert len(batch.assets) == 1
    assert batch.assets[0].mime == "image/png"
    assert batch.assets[0].data == PNG
    assert batch.model == "gpt-image-1"
    assert batch.provider_label
    post = fake_httpx.calls[0]
    assert post[1].endswith("/images/generations")
    assert post[2]["size"] == "1024x1024"
    assert post[2]["response_format"] == "b64_json"


@pytest.mark.asyncio
async def test_openai_images_downloads_url(fake_httpx):
    def post(url, body):
        return _Resp(200, {"data": [{"url": "https://cdn.example.com/a.jpg"}]})

    def get(url):
        assert url == "https://cdn.example.com/a.jpg"
        return _Resp(200, content=JPG)

    fake_httpx.routes.update({"post": post, "get": get})
    batch = await _openai().generate(images.ImageRequest(prompt="海报"))
    assert batch.assets[0].mime == "image/jpeg"
    assert batch.assets[0].data == JPG


@pytest.mark.asyncio
async def test_openai_images_auth_error(fake_httpx):
    fake_httpx.routes["post"] = lambda url, body: _Resp(401)
    with pytest.raises(AppError) as exc:
        await _openai().generate(images.ImageRequest(prompt="海报"))
    assert exc.value.code == "IMAGE_ERROR"
    assert exc.value.details["category"] == "auth"
    assert "sk-test" not in exc.value.message


@pytest.mark.asyncio
async def test_openai_images_empty_result(fake_httpx):
    fake_httpx.routes["post"] = lambda url, body: _Resp(200, {"data": []})
    with pytest.raises(AppError) as exc:
        await _openai().generate(images.ImageRequest(prompt="海报"))
    assert exc.value.details["category"] == "empty_result"


def _dashscope(**kw):
    params = {
        "base_url": "https://dashscope.example.com",
        "api_key": "sk-ds",
        "model": "wanx2.1-t2i-turbo",
        "poll_interval": 0,
    }
    params.update(kw)
    return images.DashScopeImageProvider(**params)


@pytest.mark.asyncio
async def test_dashscope_polls_until_succeeded(fake_httpx):
    polls = {"n": 0}

    def post(url, body):
        assert url.endswith("/text2image/image-synthesis")
        assert body["parameters"]["size"] == "1024*1024"
        return _Resp(200, {"output": {"task_id": "task-1", "task_status": "PENDING"}})

    def get(url):
        if url.endswith("/tasks/task-1"):
            polls["n"] += 1
            if polls["n"] < 3:
                return _Resp(200, {"output": {"task_status": "RUNNING"}})
            return _Resp(
                200,
                {"output": {"task_status": "SUCCEEDED", "results": [{"url": "https://cdn/x.png"}]}},
            )
        return _Resp(200, content=PNG)

    fake_httpx.routes.update({"post": post, "get": get})
    batch = await _dashscope().generate(images.ImageRequest(prompt="海报", size="1024x1024"))
    assert polls["n"] == 3
    assert batch.assets[0].mime == "image/png"


@pytest.mark.asyncio
async def test_dashscope_failed_task(fake_httpx):
    fake_httpx.routes["post"] = lambda url, body: _Resp(200, {"output": {"task_id": "t2"}})
    fake_httpx.routes["get"] = lambda url: _Resp(
        200, {"output": {"task_status": "FAILED", "message": "invalid prompt"}}
    )
    with pytest.raises(AppError) as exc:
        await _dashscope().generate(images.ImageRequest(prompt="海报"))
    assert "invalid prompt" in exc.value.message


@pytest.mark.asyncio
async def test_dashscope_missing_task_id(fake_httpx):
    fake_httpx.routes["post"] = lambda url, body: _Resp(200, {"output": {"message": "no quota"}})
    with pytest.raises(AppError) as exc:
        await _dashscope().generate(images.ImageRequest(prompt="海报"))
    assert "no quota" in exc.value.message


def test_normalize_size_and_count():
    assert images.normalize_size("1024*1024") == "1024x1024"
    assert images.normalize_size("1024×1536") == "1024x1536"
    assert images.normalize_size("999x999") == images.DEFAULT_SIZE
    assert images.normalize_size(None) == images.DEFAULT_SIZE
    assert images.normalize_count(0) == 1
    assert images.normalize_count(99) == images.MAX_IMAGES
    assert images.normalize_count("bad") == 1


def test_sniff_mime():
    assert images.sniff_mime(PNG) == "image/png"
    assert images.sniff_mime(JPG) == "image/jpeg"
    assert images.sniff_mime(b"RIFF0000WEBPVP8 ") == "image/webp"
    assert images.sniff_mime(b"GIF89a") == "image/gif"
    assert images.sniff_mime(b"plain text") == "application/octet-stream"


def test_build_image_provider_rejects_chat_supplier():
    class _Row:
        provider_type = "deepseek"
        api_key_enc = "x"
        base_url = "https://x"
        model_id = "m"
        name = "ds"

    with pytest.raises(AppError) as exc:
        images.build_image_provider(_Row())
    assert exc.value.code == "IMAGE_NOT_CONFIGURED"


def _create(db, yard_id, *, name, provider_type, base_url, model_id, priority=10):
    payload = SupplierCreate(
        name=name,
        provider_type=provider_type,
        base_url=base_url,
        model_id=model_id,
        api_key="sk-test-key-123",
        priority=priority,
    )
    return supplier_service.create_supplier(db, yard_id, payload, is_platform_admin=False)


def test_resolve_image_provider_skips_chat_suppliers(db_session):
    _create(
        db_session,
        "yard-img",
        name="chat-first",
        provider_type="deepseek",
        base_url="https://api.deepseek.com",
        model_id="deepseek-chat",
        priority=99,
    )
    _create(
        db_session,
        "yard-img",
        name="wanx",
        provider_type="dashscope_image",
        base_url="https://dashscope.aliyuncs.com",
        model_id="wanx2.1-t2i-turbo",
        priority=1,
    )
    resolved = images.resolve_image_provider(db_session, "yard-img")
    assert resolved is not None
    provider, level = resolved
    assert isinstance(provider, images.DashScopeImageProvider)
    assert level == "image_supplier_priority"


def test_resolve_image_provider_none_without_image_supplier(db_session):
    _create(
        db_session,
        "yard-chat",
        name="chat-only",
        provider_type="deepseek",
        base_url="https://api.deepseek.com",
        model_id="deepseek-chat",
    )
    assert images.resolve_image_provider(db_session, "yard-chat") is None


def test_visual_capabilities_and_templates():
    assert "image_generation" in CAPABILITY_CODES
    assert "video_generation" in CAPABILITY_CODES
    types = {item.provider_type for item in supplier_service.get_provider_templates()}
    assert {"openai_images", "dashscope_image", "openai_video"} <= types
    assert images.IMAGE_PROVIDER_TYPES == {"openai_images", "dashscope_image"}
    assert images.VIDEO_PROVIDER_TYPES == {"openai_video"}


@pytest.mark.asyncio
async def test_probe_upstream_visual_supplier_skips_chat(fake_httpx):
    fake_httpx.routes["get"] = lambda url: _Resp(404)
    fake_httpx.routes["post"] = lambda url, body: _Resp(200, {"choices": []})
    result = await supplier_service.probe_upstream(
        "https://img.example.com/v1",
        "sk-test",
        "gpt-image-1",
        provider_type="openai_images",
    )
    assert result.ok is False
    assert "图像/视频供应商" in result.note
    assert not [c for c in fake_httpx.calls if c[1].endswith("/chat/completions")]


@pytest.mark.asyncio
async def test_probe_upstream_visual_supplier_reports_auth(fake_httpx):
    fake_httpx.routes["get"] = lambda url: _Resp(401)
    fake_httpx.routes["post"] = lambda url, body: _Resp(200, {"choices": []})
    result = await supplier_service.probe_upstream(
        "https://img.example.com/v1",
        "sk-test",
        "gpt-image-1",
        provider_type="openai_images",
    )
    assert result.ok is False
    assert "鉴权失败" in result.note
