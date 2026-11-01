"""v3.37 图像/视频通道：模态判定、参数降级、异步任务与受理语义。

全部离线：`httpx.MockTransport` 假扮上游，不产生任何真实出网。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from app.core.errors import AppError
from app.modules.llm.images import (
    ImageRequest,
    OpenAIImagesProvider,
    build_image_provider,
    normalize_dashscope_base,
)
from app.modules.llm.video import (
    DashScopeVideoProvider,
    OpenAIVideoProvider,
    VideoRequest,
    build_video_provider,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 16


def _supplier(
    provider_type: str = "openai_compatible", base_url: str = "https://api.test.example/v1"
):
    return SimpleNamespace(
        id="sup-1",
        name="测试供应商",
        provider_type=provider_type,
        base_url=base_url,
        model_id="default-model",
        api_key_enc="enc",
    )


def _model(modality: str = "image", protocol: str = "openai_chat", endpoint_path=None):
    return SimpleNamespace(
        model_key="m1",
        upstream_model_id="m1-upstream",
        modality=modality,
        protocol=protocol,
        endpoint_path=endpoint_path,
    )


# ---------- 基址归一化 ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", "https://dashscope.aliyuncs.com"),
        ("https://dashscope.aliyuncs.com/compatible-mode", "https://dashscope.aliyuncs.com"),
        ("https://dashscope.aliyuncs.com/v1", "https://dashscope.aliyuncs.com"),
        ("https://dashscope.aliyuncs.com", "https://dashscope.aliyuncs.com"),
        ("", "https://dashscope.aliyuncs.com"),
    ],
)
def test_normalize_dashscope_base(raw, expected):
    assert normalize_dashscope_base(raw) == expected


# ---------- 图像：模态优先，协议不再一票否决 ----------


def test_image_provider_accepts_generic_supplier_by_modality(monkeypatch):
    monkeypatch.setattr("app.modules.llm.supplier_crypto.decrypt_api_key", lambda _enc: "sk-test")
    provider = build_image_provider(_supplier(), model_row=_model(modality="image"))
    assert isinstance(provider, OpenAIImagesProvider)
    assert provider.model == "m1-upstream"


def test_image_provider_rejects_non_image_modality(monkeypatch):
    monkeypatch.setattr("app.modules.llm.supplier_crypto.decrypt_api_key", lambda _enc: "sk-test")
    with pytest.raises(AppError) as exc:
        build_image_provider(_supplier(), model_row=_model(modality="text"))
    assert "不提供图像生成能力" in exc.value.message


def test_image_provider_prefers_dashscope_protocol(monkeypatch):
    from app.modules.llm.images import DashScopeImageProvider

    monkeypatch.setattr("app.modules.llm.supplier_crypto.decrypt_api_key", lambda _enc: "sk-test")
    provider = build_image_provider(
        _supplier(base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"),
        model_row=_model(modality="image", protocol="dashscope_image"),
    )
    assert isinstance(provider, DashScopeImageProvider)
    assert provider.base_url == "https://dashscope.aliyuncs.com"


# ---------- 图像：400 自动降级参数 ----------


async def test_image_provider_retries_with_minimal_payload_and_parses_url():
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, content=PNG_BYTES)
        payload = json.loads(request.content)
        calls.append(payload)
        if "response_format" in payload:
            return httpx.Response(400, json={"error": {"message": "unsupported response_format"}})
        return httpx.Response(200, json={"data": [{"url": "https://cdn.test.example/a.png"}]})

    provider = OpenAIImagesProvider(
        base_url="https://api.test.example/v1",
        api_key="sk-test",
        model="m1",
        transport=httpx.MockTransport(handler),
    )
    batch = await provider.generate(ImageRequest(prompt="ping"))
    assert batch.assets[0].data.startswith(b"\x89PNG")
    assert "response_format" in calls[0] and "response_format" not in calls[1]


async def test_image_provider_parses_dashscope_style_output():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, content=PNG_BYTES)
        return httpx.Response(
            200,
            json={"output": {"results": [{"url": "https://cdn.test.example/b.png"}]}},
        )

    provider = OpenAIImagesProvider(
        base_url="https://api.test.example/v1",
        api_key="sk-test",
        model="m1",
        transport=httpx.MockTransport(handler),
    )
    batch = await provider.generate(ImageRequest(prompt="ping"))
    assert batch.assets[0].data.startswith(b"\x89PNG")


async def test_image_provider_error_carries_upstream_excerpt():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "size not supported"}})

    provider = OpenAIImagesProvider(
        base_url="https://api.test.example/v1",
        api_key="sk-test",
        model="m1",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(AppError) as exc:
        await provider.generate(ImageRequest(prompt="ping"))
    assert "size not supported" in exc.value.message


# ---------- 视频 ----------


def test_video_provider_accepts_generic_supplier_by_modality(monkeypatch):
    monkeypatch.setattr("app.modules.llm.supplier_crypto.decrypt_api_key", lambda _enc: "sk-test")
    provider = build_video_provider(_supplier(), model_row=_model(modality="video"))
    assert isinstance(provider, OpenAIVideoProvider)


def test_video_provider_rejects_non_video_modality(monkeypatch):
    monkeypatch.setattr("app.modules.llm.supplier_crypto.decrypt_api_key", lambda _enc: "sk-test")
    with pytest.raises(AppError) as exc:
        build_video_provider(_supplier(), model_row=_model(modality="text"))
    assert "不提供视频生成能力" in exc.value.message


async def test_video_openai_polls_until_completed():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/videos"):
            return httpx.Response(200, json={"id": "task-1", "status": "queued"})
        if request.method == "GET" and request.url.path.endswith("/videos/task-1"):
            return httpx.Response(
                200,
                json={
                    "id": "task-1",
                    "status": "completed",
                    "url": "https://cdn.test.example/v.mp4",
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    provider = OpenAIVideoProvider(
        base_url="https://api.test.example/v1",
        api_key="sk-test",
        model="v1",
        poll_interval=0,
        transport=httpx.MockTransport(handler),
    )
    batch = await provider.generate(VideoRequest(prompt="一只猫"), wait_seconds=5)
    assert batch.assets[0].url == "https://cdn.test.example/v.mp4"
    assert batch.accepted is False


async def test_video_openai_falls_back_to_video_generations_path():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.method == "POST" and request.url.path.endswith("/videos"):
            return httpx.Response(404, json={"error": "no such path"})
        if request.method == "POST" and request.url.path.endswith("/video/generations"):
            return httpx.Response(200, json={"task_id": "t2"})
        if request.method == "GET" and request.url.path.endswith("/video/generations/t2"):
            return httpx.Response(
                200,
                json={
                    "task_id": "t2",
                    "status": "succeeded",
                    "output": {"results": [{"url": "https://cdn.test.example/v2.mp4"}]},
                },
            )
        return httpx.Response(404, json={})

    provider = OpenAIVideoProvider(
        base_url="https://api.test.example/v1",
        api_key="sk-test",
        model="v1",
        poll_interval=0,
        transport=httpx.MockTransport(handler),
    )
    batch = await provider.generate(VideoRequest(prompt="一只猫"), wait_seconds=5)
    assert batch.assets[0].url == "https://cdn.test.example/v2.mp4"
    assert any("/video/generations" in item for item in seen)


async def test_video_openai_accepted_when_still_running():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "task-3", "status": "queued"})
        return httpx.Response(200, json={"id": "task-3", "status": "in_progress"})

    provider = OpenAIVideoProvider(
        base_url="https://api.test.example/v1",
        api_key="sk-test",
        model="v1",
        poll_interval=0,
        transport=httpx.MockTransport(handler),
    )
    # 等待窗口 0 秒：不上轮询，直接按「已受理」返回
    batch = await provider.generate(VideoRequest(prompt="一只猫"), wait_seconds=0)
    assert batch.accepted is True
    assert batch.task_id == "task-3"
    assert batch.assets == ()


async def test_video_dashscope_completes_with_url():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"output": {"task_id": "ds-1"}})
        return httpx.Response(
            200,
            json={
                "output": {
                    "task_status": "SUCCEEDED",
                    "video_url": "https://cdn.test.example/ds.mp4",
                }
            },
        )

    provider = DashScopeVideoProvider(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="sk-test",
        model="wan2.2-t2v-plus",
        poll_interval=0,
        transport=httpx.MockTransport(handler),
    )
    batch = await provider.generate(VideoRequest(prompt="一只猫"), wait_seconds=5)
    assert batch.assets[0].url == "https://cdn.test.example/ds.mp4"
