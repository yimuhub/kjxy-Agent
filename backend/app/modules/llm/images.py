"""图像 / 视频生成 Provider（v3.26 D-1.4）。

口径：**所有生成式视觉能力都由模型供应商供应**，平台不自建出图服务、不内置绘图
模型权重；供应商仍走 3.25 的绑定/优先级/兜底链，只是多了一类「不是 chat」的
协议适配：

- ``openai_images``：任意兼容 ``POST {base_url}/images/generations`` 的服务
  （OpenAI、Nano-Banana 等），支持 ``b64_json`` 与 ``url`` 两种返回；
- ``dashscope_image``：阿里云百炼原生异步接口（提交任务 → 轮询 → 取结果 URL）；
- ``openai_video``：视频生成通道**只登记协议位**，尚未挂内置工具（无供应商可验证时
  不写猜测性实现）。

``provider_type`` 是 ``suppliers`` 表里的字符串列，新增取值不需要迁移。
密钥只出现在 Authorization 头，绝不落日志、绝不进错误 details。
"""

from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger(__name__)

IMAGE_PROVIDER_TYPES = frozenset({"openai_images", "dashscope_image"})
VIDEO_PROVIDER_TYPES = frozenset({"openai_video"})
#: 图像供应商类型 → 中文名（前端模板与错误文案共用）
IMAGE_PROVIDER_LABELS: dict[str, str] = {
    "openai_images": "图像生成（OpenAI 兼容）",
    "dashscope_image": "图像生成（阿里云百炼原生）",
    "openai_video": "视频生成（通道预留）",
}

DEFAULT_SIZE = "1024x1024"
#: 允许的出图尺寸：与前端控件和常见上游一致，避免把任意字符串透传给上游
ALLOWED_SIZES = (
    "1024x1024",
    "1536x1024",
    "1024x1536",
    "768x1024",
    "1024x768",
    "1280x720",
    "720x1280",
)
MAX_IMAGES = 4
MAX_PROMPT_LEN = 2000


@dataclass(frozen=True)
class ImageRequest:
    prompt: str
    size: str = DEFAULT_SIZE
    n: int = 1
    negative_prompt: str | None = None


@dataclass(frozen=True)
class ImageAsset:
    data: bytes
    mime: str


@dataclass(frozen=True)
class ImageBatch:
    assets: tuple[ImageAsset, ...]
    provider_label: str
    model: str
    latency_ms: int


class ImagesProvider(Protocol):
    async def generate(self, request: ImageRequest) -> ImageBatch: ...


def image_error(message: str, *, category: str = "provider_error") -> AppError:
    return AppError("IMAGE_ERROR", message, http_status=502, details={"category": category})


def normalize_size(size: str | None) -> str:
    """归一化尺寸：``1024*1024``/``1024×1024`` 都收，非法值回落默认。"""
    raw = (size or "").strip().lower().replace("*", "x").replace("×", "x")
    return raw if raw in ALLOWED_SIZES else DEFAULT_SIZE


def normalize_count(n: int | None) -> int:
    try:
        value = int(n or 1)
    except (TypeError, ValueError):
        value = 1
    return max(1, min(value, MAX_IMAGES))


def sniff_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"GIF8"):
        return "image/gif"
    return "application/octet-stream"


def _truncate(text: str, limit: int = 160) -> str:
    clean = " ".join(str(text or "").split())
    return clean[:limit]


class OpenAIImagesProvider:
    """OpenAI 兼容的图像生成接口。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int = 120,
        provider_label: str = "images",
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout_seconds
        self.provider_label = provider_label
        if not self.base_url or not self.api_key or not self.model:
            raise AppError(
                "IMAGE_NOT_CONFIGURED",
                "图像生成供应商未配置：缺少 base_url / api_key / model。",
                http_status=503,
            )

    async def generate(self, request: ImageRequest) -> ImageBatch:
        from app.core.net_guard import check_egress_url

        url = f"{self.base_url}/images/generations"
        check_egress_url(url, context="image_generation", allow_local=True)
        payload = {
            "model": self.model,
            "prompt": request.prompt[:MAX_PROMPT_LEN],
            "size": normalize_size(request.size),
            "n": normalize_count(request.n),
            "response_format": "b64_json",
        }
        if request.negative_prompt:
            # OpenAI 协议没有负面词字段；部分兼容实现接受 prompt 内嵌写法
            payload["prompt"] = f"{payload['prompt']}\n\nAvoid: {request.negative_prompt[:400]}"
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                if resp.status_code in (401, 403):
                    raise image_error("图像服务鉴权失败，请检查供应商密钥。", category="auth")
                if resp.status_code == 429:
                    raise image_error("图像服务限流，请稍后重试。", category="rate_limit")
                if 500 <= resp.status_code < 600:
                    raise image_error("图像服务暂时不可用，请稍后重试。", category="provider_5xx")
                if resp.status_code != 200:
                    raise image_error("图像服务返回异常，请稍后重试。", category="provider_4xx")
                try:
                    data = resp.json()
                except ValueError as e:
                    raise image_error("图像服务返回无法解析。", category="provider_5xx") from e
                if not isinstance(data, dict):
                    raise image_error("图像服务返回格式异常。", category="provider_5xx")
                assets: list[ImageAsset] = []
                for item in data.get("data") or []:
                    asset = await self._asset_from_item(client, item)
                    if asset is not None:
                        assets.append(asset)
        except httpx.TimeoutException as e:
            raise image_error(
                f"图像生成超时（{self.timeout} 秒），请稍后重试。", category="timeout"
            ) from e
        except httpx.HTTPError as e:
            raise image_error(
                "无法连接图像服务，请检查网络与服务地址。", category="connection"
            ) from e
        if not assets:
            raise image_error("图像服务未返回任何图片。", category="empty_result")
        latency_ms = int((time.perf_counter() - started) * 1000)
        return ImageBatch(
            assets=tuple(assets),
            provider_label=self.provider_label,
            model=self.model,
            latency_ms=latency_ms,
        )

    async def _asset_from_item(self, client: httpx.AsyncClient, item: object) -> ImageAsset | None:
        if not isinstance(item, dict):
            return None
        b64 = item.get("b64_json")
        if isinstance(b64, str) and b64:
            try:
                raw = base64.b64decode(b64, validate=False)
            except (ValueError, TypeError):
                return None
            return ImageAsset(data=raw, mime=sniff_mime(raw))
        remote = item.get("url")
        if isinstance(remote, str) and remote.startswith(("http://", "https://")):
            return await self._download(client, remote)
        return None

    async def _download(self, client: httpx.AsyncClient, url: str) -> ImageAsset | None:
        from app.core.net_guard import check_egress_url

        check_egress_url(url, context="image_download", allow_local=True)
        try:
            resp = await client.get(url)
        except httpx.HTTPError:
            return None
        if resp.status_code != 200 or not resp.content:
            return None
        return ImageAsset(data=resp.content, mime=sniff_mime(resp.content))


class DashScopeImageProvider:
    """阿里云百炼原生文生图：提交异步任务 → 轮询 → 下载结果。

    百炼的同步兼容端点只覆盖对话/向量，文生图走 ``X-DashScope-Async`` 异步任务，
    因此这里必须显式轮询；超时/失败都以 ``IMAGE_ERROR`` 暴露，不返回半成品。
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int = 150,
        poll_interval: float = 2.0,
        provider_label: str = "dashscope",
    ) -> None:
        self.base_url = (base_url or "https://dashscope.aliyuncs.com").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout_seconds
        self.poll_interval = poll_interval
        self.provider_label = provider_label
        if not self.api_key or not self.model:
            raise AppError(
                "IMAGE_NOT_CONFIGURED",
                "图像生成供应商未配置：缺少 api_key 或 model。",
                http_status=503,
            )

    async def generate(self, request: ImageRequest) -> ImageBatch:
        from app.core.net_guard import check_egress_url

        started = time.perf_counter()
        submit_url = f"{self.base_url}/api/v1/services/aigc/text2image/image-synthesis"
        check_egress_url(submit_url, context="image_generation", allow_local=True)
        payload: dict = {
            "model": self.model,
            "input": {"prompt": request.prompt[:MAX_PROMPT_LEN]},
            "parameters": {
                "size": normalize_size(request.size).replace("x", "*"),
                "n": normalize_count(request.n),
            },
        }
        if request.negative_prompt:
            payload["input"]["negative_prompt"] = request.negative_prompt[:400]
        headers = {"Authorization": f"Bearer {self.api_key}", "X-DashScope-Async": "enable"}
        deadline = time.monotonic() + self.timeout
        try:
            async with httpx.AsyncClient(timeout=min(self.timeout, 60)) as client:
                resp = await client.post(submit_url, json=payload, headers=headers)
                task_id = self._task_id(resp)
                urls = await self._poll(client, task_id, deadline)
                assets: list[ImageAsset] = []
                for url in urls:
                    asset = await self._download(client, url)
                    if asset is not None:
                        assets.append(asset)
        except httpx.TimeoutException as e:
            raise image_error(
                f"图像生成超时（{self.timeout} 秒），请稍后重试。", category="timeout"
            ) from e
        except httpx.HTTPError as e:
            raise image_error(
                "无法连接图像服务，请检查网络与服务地址。", category="connection"
            ) from e
        if not assets:
            raise image_error("图像服务未返回任何图片。", category="empty_result")
        latency_ms = int((time.perf_counter() - started) * 1000)
        return ImageBatch(
            assets=tuple(assets),
            provider_label=self.provider_label,
            model=self.model,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _task_id(resp: httpx.Response) -> str:
        if resp.status_code in (401, 403):
            raise image_error("图像服务鉴权失败，请检查供应商密钥。", category="auth")
        if resp.status_code == 429:
            raise image_error("图像服务限流，请稍后重试。", category="rate_limit")
        if resp.status_code != 200:
            raise image_error("图像服务返回异常，请稍后重试。", category="provider_4xx")
        try:
            data = resp.json()
        except ValueError as e:
            raise image_error("图像服务返回无法解析。", category="provider_5xx") from e
        if not isinstance(data, dict):
            raise image_error("图像服务返回格式异常。", category="provider_5xx")
        task_id = str((data.get("output") or {}).get("task_id") or "")
        if not task_id:
            message = _truncate(
                str((data.get("output") or {}).get("message") or data.get("message") or "")
            )
            raise image_error(f"图像任务提交失败：{message or '未返回 task_id'}。")
        return task_id

    async def _poll(self, client: httpx.AsyncClient, task_id: str, deadline: float) -> list[str]:
        url = f"{self.base_url}/api/v1/tasks/{task_id}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        while True:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                raise image_error("图像任务查询失败，请稍后重试。", category="provider_4xx")
            try:
                data = resp.json()
            except ValueError as e:
                raise image_error("图像服务返回无法解析。", category="provider_5xx") from e
            output = data.get("output") or {}
            status = str(output.get("task_status") or "").upper()
            if status == "SUCCEEDED":
                return [
                    str(item.get("url"))
                    for item in (output.get("results") or [])
                    if isinstance(item, dict) and item.get("url")
                ]
            if status in {"FAILED", "CANCELED", "UNKNOWN"}:
                message = _truncate(output.get("message") or "")
                raise image_error(f"图像任务失败：{message or status}。")
            if time.monotonic() >= deadline:
                raise image_error("图像任务超时，请稍后重试。", category="timeout")
            await asyncio.sleep(self.poll_interval)

    async def _download(self, client: httpx.AsyncClient, url: str) -> ImageAsset | None:
        from app.core.net_guard import check_egress_url

        check_egress_url(url, context="image_download", allow_local=True)
        try:
            resp = await client.get(url)
        except httpx.HTTPError:
            return None
        if resp.status_code != 200 or not resp.content:
            return None
        return ImageAsset(data=resp.content, mime=sniff_mime(resp.content))


def build_image_provider(supplier, *, model_override: str | None = None) -> ImagesProvider:  # noqa: ANN001 - 入参是 ORM 行，避免循环导入
    """按供应商行构建图像 Provider；chat 类供应商在这里一律拒绝。"""
    from app.modules.llm.supplier_crypto import decrypt_api_key

    provider_type = str(getattr(supplier, "provider_type", "") or "")
    if provider_type not in IMAGE_PROVIDER_TYPES:
        raise AppError(
            "IMAGE_NOT_CONFIGURED",
            f"供应商 {provider_type or '未知'} 不提供图像生成能力。",
            http_status=503,
            details={"provider_type": provider_type},
        )
    api_key = decrypt_api_key(supplier.api_key_enc)
    try:
        kwargs = {
            "base_url": supplier.base_url,
            "api_key": api_key,
            "model": (model_override or "").strip() or supplier.model_id,
            "provider_label": f"supplier:{supplier.name}"[:50],
        }
        if provider_type == "dashscope_image":
            return DashScopeImageProvider(**kwargs)
        return OpenAIImagesProvider(**kwargs)
    finally:
        del api_key


def resolve_image_provider(
    db,  # noqa: ANN001
    yard_id: str,
    *,
    agent_code: str | None = None,
    bound_supplier_id: str | None = None,
) -> tuple[ImagesProvider, str] | None:
    """沿 3.25 绑定链找**图像类**供应商；链上全是 chat 供应商时按可见性+优先级回落。

    返回 ``(provider, 命中层级)``；一个可用的图像供应商都没有时返回 ``None``
    （调用方据此降级模板渲染并如实告知，而不是报错卡死）。
    """
    from sqlalchemy import or_

    from app.modules.llm import binding_service
    from app.modules.llm.supplier_models import Supplier

    chain = binding_service.resolve_chain(
        db,
        yard_id,
        purpose="image_generation",
        agent_code=agent_code,
        bound_supplier_id=bound_supplier_id,
    )
    seen: set[str] = set()
    for candidate in chain:
        row = candidate.supplier
        if getattr(row, "provider_type", "") not in IMAGE_PROVIDER_TYPES:
            continue
        if row.id in seen:
            continue
        seen.add(row.id)
        return build_image_provider(row, model_override=candidate.model_name), candidate.level

    row = (
        db.query(Supplier)
        .filter(
            Supplier.status == 1,
            Supplier.provider_type.in_(sorted(IMAGE_PROVIDER_TYPES)),
            or_(Supplier.yard_id == yard_id, Supplier.yard_id.is_(None)),
        )
        .order_by(Supplier.priority.desc(), Supplier.created_at.asc())
        .first()
    )
    if row is None:
        return None
    return build_image_provider(row), "image_supplier_priority"


__all__ = [
    "ALLOWED_SIZES",
    "DEFAULT_SIZE",
    "IMAGE_PROVIDER_LABELS",
    "IMAGE_PROVIDER_TYPES",
    "MAX_IMAGES",
    "MAX_PROMPT_LEN",
    "VIDEO_PROVIDER_TYPES",
    "DashScopeImageProvider",
    "ImageAsset",
    "ImageBatch",
    "ImageRequest",
    "ImagesProvider",
    "OpenAIImagesProvider",
    "build_image_provider",
    "image_error",
    "normalize_count",
    "normalize_size",
    "resolve_image_provider",
    "sniff_mime",
]
