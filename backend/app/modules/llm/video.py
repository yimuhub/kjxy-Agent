"""视频生成 Provider（v3.37）。

口径与图像一致：**生成式能力全部由模型供应商供应**，平台不内置视频模型权重。
两种协议适配：

- ``openai_video``：OpenAI 兼容异步任务。先试 ``POST {base}/videos``（Sora 协议），
  404 时退回 ``POST {base}/video/generations``（国内聚合网关常见形态）；
  任务受理后按两种路径轮询，拿到成品 URL 即返回。
- ``dashscope_video``：阿里云百炼万相视频（``X-DashScope-Async`` 异步任务）。

**受理即连通**：视频生成通常要几十秒到几分钟。调用方可以给 ``wait_seconds`` 设上限，
超时未出片时返回 ``accepted=True`` + ``task_id``（上游已受理，任务在跑），
既不假装成功出片，也不把「可用」误判成「不可用」。

密钥只出现在 Authorization 头，绝不落日志、绝不进错误 details。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.llm.images import normalize_dashscope_base, upstream_excerpt

log = get_logger(__name__)

VIDEO_PROVIDER_TYPES = frozenset({"openai_video", "dashscope_video"})
VIDEO_PROVIDER_LABELS: dict[str, str] = {
    "openai_video": "视频生成（OpenAI 兼容）",
    "dashscope_video": "视频生成（阿里云百炼）",
}

DEFAULT_DURATION_SECONDS = 5
MAX_DURATION_SECONDS = 30
MAX_PROMPT_LEN = 2000
DEFAULT_SIZE = "1280x720"


@dataclass(frozen=True)
class VideoRequest:
    prompt: str
    duration_seconds: int = DEFAULT_DURATION_SECONDS
    size: str = DEFAULT_SIZE
    image_url: str | None = None


@dataclass(frozen=True)
class VideoAsset:
    url: str
    mime: str = "video/mp4"


@dataclass(frozen=True)
class VideoBatch:
    assets: tuple[VideoAsset, ...]
    provider_label: str
    model: str
    latency_ms: int
    task_id: str | None = None
    #: True = 上游已受理但未在等待窗口内出片（任务仍在跑）
    accepted: bool = False
    message: str = ""


class VideosProvider(Protocol):
    async def generate(self, request: VideoRequest, *, wait_seconds: int = 600) -> VideoBatch: ...


def video_error(message: str, *, category: str = "provider_error") -> AppError:
    return AppError("VIDEO_ERROR", message, http_status=502, details={"category": category})


def _normalize_duration(value: int | None) -> int:
    try:
        seconds = int(value or DEFAULT_DURATION_SECONDS)
    except (TypeError, ValueError):
        seconds = DEFAULT_DURATION_SECONDS
    return max(1, min(seconds, MAX_DURATION_SECONDS))


def _normalize_path(path: str | None, default: str) -> str:
    raw = (path or "").strip()
    if not raw:
        return default
    return raw if raw.startswith("/") else f"/{raw}"


def _task_id_of(data: Any) -> str:
    """兼容多种受理回包：{id} / {task_id} / {data:{task_id}} / {output:{task_id}}。"""
    if not isinstance(data, dict):
        return ""
    for key in ("id", "task_id", "taskId"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    for key in ("data", "output"):
        nested = data.get(key)
        if isinstance(nested, dict):
            found = _task_id_of(nested)
            if found:
                return found
    return ""


def _video_url_of(data: Any) -> str:
    """从回包里找成品地址：url / video_url / output.results[].url。"""
    if not isinstance(data, dict):
        return ""
    for key in ("url", "video_url", "videoUrl", "output_url"):
        value = data.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    for key in ("data", "output", "result", "results", "videos"):
        nested = data.get(key)
        if isinstance(nested, dict):
            found = _video_url_of(nested)
            if found:
                return found
        elif isinstance(nested, list):
            for item in nested:
                found = _video_url_of(item)
                if found:
                    return found
    return ""


def _status_of(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("status", "task_status", "state"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value.upper()
    for key in ("data", "output"):
        nested = data.get(key)
        if isinstance(nested, dict):
            found = _status_of(nested)
            if found:
                return found
    return ""


_DONE_STATUSES = {"COMPLETED", "SUCCEEDED", "SUCCESS", "SUCCESSFUL", "FINISHED", "DONE"}
_FAILED_STATUSES = {"FAILED", "FAILURE", "CANCELED", "CANCELLED", "ERROR", "UNKNOWN"}


class OpenAIVideoProvider:
    """OpenAI 兼容视频：/videos（Sora）优先，404 退回 /video/generations。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int = 120,
        provider_label: str = "video",
        endpoint_path: str | None = None,
        poll_interval: float = 3.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout_seconds
        self.provider_label = provider_label
        self.endpoint_path = _normalize_path(endpoint_path, "/videos")
        self.poll_interval = poll_interval
        self.transport = transport
        if not self.base_url or not self.api_key or not self.model:
            raise AppError(
                "VIDEO_NOT_CONFIGURED",
                "视频生成供应商未配置：缺少 base_url / api_key / model。",
                http_status=503,
            )

    @property
    def _candidate_paths(self) -> list[str]:
        if self.endpoint_path != "/videos":
            return [self.endpoint_path]
        return ["/videos", "/video/generations"]

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def _submit(self, client: httpx.AsyncClient, request: VideoRequest) -> tuple[str, dict]:
        """提交任务：返回 (命中的接口路径, 响应 JSON)。"""
        from app.core.net_guard import check_egress_url

        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": request.prompt[:MAX_PROMPT_LEN],
            "seconds": _normalize_duration(request.duration_seconds),
        }
        if request.image_url:
            payload["image_url"] = request.image_url
        for path in self._candidate_paths:
            url = f"{self.base_url}{path}"
            check_egress_url(url, context="video_generation", allow_local=True)
            resp = await client.post(url, json=payload, headers=self._headers())
            if resp.status_code == 404:
                continue
            if resp.status_code in (401, 403):
                raise video_error("视频服务鉴权失败，请检查供应商密钥。", category="auth")
            if resp.status_code == 429:
                raise video_error("视频服务限流，请稍后重试。", category="rate_limit")
            if resp.status_code >= 500:
                raise video_error("视频服务暂时不可用，请稍后重试。", category="provider_5xx")
            if resp.status_code != 200:
                raise video_error(
                    f"视频任务提交失败（{resp.status_code}）："
                    f"{upstream_excerpt(resp) or '无响应体'}",
                    category="provider_4xx",
                )
            try:
                data = resp.json()
            except ValueError as e:
                raise video_error(
                    f"视频服务返回无法解析：{upstream_excerpt(resp) or '无响应体'}",
                    category="provider_5xx",
                ) from e
            return path, data if isinstance(data, dict) else {}
        raise video_error(
            "视频接口路径不存在（/videos 与 /video/generations 都返回 404），"
            "请在模型设置里指定「接口路径」。",
            category="provider_4xx",
        )

    def _poll_paths(self, submit_path: str, task_id: str) -> list[str]:
        if submit_path.endswith("/videos"):
            return [f"/videos/{task_id}", f"/videos/{task_id}/content"]
        return [f"{submit_path}/{task_id}", f"/videos/{task_id}"]

    async def generate(self, request: VideoRequest, *, wait_seconds: int = 600) -> VideoBatch:
        started = time.perf_counter()
        deadline = time.monotonic() + max(0, wait_seconds)
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                submit_path, data = await self._submit(client, request)
                url = _video_url_of(data)
                task_id = _task_id_of(data)
                if url:
                    return self._done(started, url, task_id)
                if not task_id:
                    raise video_error(
                        "视频任务已受理但上游没有返回任务 id，无法查询进度。",
                        category="provider_5xx",
                    )
                while time.monotonic() < deadline:
                    await asyncio.sleep(self.poll_interval)
                    polled = await self._poll(client, submit_path, task_id)
                    if polled is None:
                        continue
                    status = _status_of(polled)
                    found = _video_url_of(polled)
                    if found and (not status or status in _DONE_STATUSES):
                        return self._done(started, found, task_id)
                    if status in _FAILED_STATUSES:
                        message = upstream_excerpt_text(polled)
                        raise video_error(
                            f"视频任务失败：{message or status}。", category="provider_4xx"
                        )
                return self._accepted(started, task_id)
        except httpx.TimeoutException as e:
            raise video_error(
                f"视频服务超时（{self.timeout} 秒），请稍后重试。", category="timeout"
            ) from e
        except httpx.HTTPError as e:
            raise video_error(
                "无法连接视频服务，请检查网络与服务地址。", category="connection"
            ) from e

    async def _poll(self, client: httpx.AsyncClient, submit_path: str, task_id: str) -> dict | None:
        from app.core.net_guard import check_egress_url

        for path in self._poll_paths(submit_path, task_id):
            url = f"{self.base_url}{path}"
            check_egress_url(url, context="video_generation", allow_local=True)
            resp = await client.get(url, headers=self._headers())
            if resp.status_code == 404:
                continue
            if resp.status_code in (401, 403):
                raise video_error("视频服务鉴权失败，请检查供应商密钥。", category="auth")
            if resp.status_code != 200:
                # 任务查询偶发 5xx：继续等，不把一次抖动判死刑
                return None
            try:
                data = resp.json()
            except ValueError:
                return None
            return data if isinstance(data, dict) else None
        return None

    def _done(self, started: float, url: str, task_id: str) -> VideoBatch:
        return VideoBatch(
            assets=(VideoAsset(url=url),),
            provider_label=self.provider_label,
            model=self.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            task_id=task_id or None,
            message="视频已生成。",
        )

    def _accepted(self, started: float, task_id: str) -> VideoBatch:
        return VideoBatch(
            assets=(),
            provider_label=self.provider_label,
            model=self.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            task_id=task_id,
            accepted=True,
            message="上游已受理，视频仍在生成（未在等待窗口内完成）。",
        )


class DashScopeVideoProvider:
    """阿里云百炼万相视频：异步任务提交 → 轮询 → 成品 URL。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int = 120,
        provider_label: str = "dashscope-video",
        poll_interval: float = 3.0,
        endpoint_path: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = normalize_dashscope_base(base_url)
        self.api_key = api_key
        self.model = model
        self.timeout = timeout_seconds
        self.provider_label = provider_label
        self.poll_interval = poll_interval
        self.endpoint_path = _normalize_path(
            endpoint_path, "/api/v1/services/aigc/video-generation/video-synthesis"
        )
        self.transport = transport
        if not self.api_key or not self.model:
            raise AppError(
                "VIDEO_NOT_CONFIGURED",
                "视频生成供应商未配置：缺少 api_key 或 model。",
                http_status=503,
            )

    def _headers(self, *, submit: bool = False) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if submit:
            headers["X-DashScope-Async"] = "enable"
        return headers

    async def generate(self, request: VideoRequest, *, wait_seconds: int = 600) -> VideoBatch:
        from app.core.net_guard import check_egress_url

        started = time.perf_counter()
        deadline = time.monotonic() + max(0, wait_seconds)
        payload: dict[str, Any] = {
            "model": self.model,
            "input": {"prompt": request.prompt[:MAX_PROMPT_LEN]},
            "parameters": {
                "duration": _normalize_duration(request.duration_seconds),
                "size": request.size.replace("x", "*"),
            },
        }
        if request.image_url:
            payload["input"]["img_url"] = request.image_url
        check_egress_url(
            f"{self.base_url}{self.endpoint_path}", context="video_generation", allow_local=True
        )
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                resp = await client.post(
                    f"{self.base_url}{self.endpoint_path}",
                    json=payload,
                    headers=self._headers(submit=True),
                )
                if resp.status_code in (401, 403):
                    raise video_error("视频服务鉴权失败，请检查供应商密钥。", category="auth")
                if resp.status_code == 429:
                    raise video_error("视频服务限流，请稍后重试。", category="rate_limit")
                if resp.status_code != 200:
                    raise video_error(
                        f"视频任务提交失败（{resp.status_code}）："
                        f"{upstream_excerpt(resp) or '无响应体'}",
                        category="provider_4xx",
                    )
                try:
                    data = resp.json()
                except ValueError as e:
                    raise video_error("视频服务返回无法解析。", category="provider_5xx") from e
                task_id = _task_id_of(data)
                if not task_id:
                    raise video_error("视频任务提交失败：未返回 task_id。", category="provider_5xx")
                while time.monotonic() < deadline:
                    await asyncio.sleep(self.poll_interval)
                    detail = await self._poll(client, task_id)
                    if detail is None:
                        continue
                    status = _status_of(detail)
                    url = _video_url_of(detail)
                    if status in _DONE_STATUSES and url:
                        return VideoBatch(
                            assets=(VideoAsset(url=url),),
                            provider_label=self.provider_label,
                            model=self.model,
                            latency_ms=int((time.perf_counter() - started) * 1000),
                            task_id=task_id,
                            message="视频已生成。",
                        )
                    if status in _FAILED_STATUSES:
                        raise video_error(
                            f"视频任务失败：{upstream_excerpt_text(detail) or status}。",
                            category="provider_4xx",
                        )
                return VideoBatch(
                    assets=(),
                    provider_label=self.provider_label,
                    model=self.model,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    task_id=task_id,
                    accepted=True,
                    message="上游已受理，视频仍在生成（未在等待窗口内完成）。",
                )
        except httpx.TimeoutException as e:
            raise video_error(
                f"视频服务超时（{self.timeout} 秒），请稍后重试。", category="timeout"
            ) from e
        except httpx.HTTPError as e:
            raise video_error(
                "无法连接视频服务，请检查网络与服务地址。", category="connection"
            ) from e

    async def _poll(self, client: httpx.AsyncClient, task_id: str) -> dict | None:
        resp = await client.get(f"{self.base_url}/api/v1/tasks/{task_id}", headers=self._headers())
        if resp.status_code == 401:
            raise video_error("视频服务鉴权失败，请检查供应商密钥。", category="auth")
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) else None


def upstream_excerpt_text(data: Any, limit: int = 160) -> str:
    """从 JSON 回包里取上游给的中文原因。"""
    if not isinstance(data, dict):
        return ""
    for key in ("message", "msg", "error_message", "detail"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:limit]
    for key in ("output", "data", "error"):
        nested = data.get(key)
        if isinstance(nested, dict):
            found = upstream_excerpt_text(nested, limit)
            if found:
                return found
        if isinstance(nested, str) and nested.strip():
            return " ".join(nested.split())[:limit]
    return ""


def build_video_provider(
    supplier,  # noqa: ANN001 - ORM 行，避免循环导入
    *,
    model_override: str | None = None,
    model_row=None,  # noqa: ANN001 - SupplierModel 行
) -> VideosProvider:
    """按供应商行/模型行构建视频 Provider；模态为 video 的通用供应商按 OpenAI 兼容处理。"""
    from app.modules.llm.supplier_crypto import decrypt_api_key

    provider_type = str(getattr(supplier, "provider_type", "") or "")
    model_protocol = str(getattr(model_row, "protocol", "") or "")
    modality = str(getattr(model_row, "modality", "") or "").lower()
    if model_protocol == "dashscope_video" or provider_type == "dashscope_video":
        protocol = "dashscope_video"
    elif model_protocol in VIDEO_PROVIDER_TYPES or provider_type in VIDEO_PROVIDER_TYPES:
        protocol = "openai_video"
    elif modality == "video":
        protocol = "openai_video"
    else:
        raise AppError(
            "VIDEO_NOT_CONFIGURED",
            f"供应商 {provider_type or '未知'} 不提供视频生成能力"
            f"（模型模态：{modality or '未知'}）。",
            http_status=503,
            details={
                "provider_type": provider_type,
                "protocol": model_protocol or None,
                "modality": modality or None,
            },
        )
    model = (
        str(getattr(model_row, "upstream_model_id", "") or "").strip()
        or (model_override or "").strip()
        or supplier.model_id
    )
    api_key = decrypt_api_key(supplier.api_key_enc)
    try:
        kwargs: dict[str, Any] = {
            "base_url": supplier.base_url,
            "api_key": api_key,
            "model": model,
            "provider_label": f"supplier:{supplier.name}"[:50],
            "endpoint_path": getattr(model_row, "endpoint_path", None),
        }
        if protocol == "dashscope_video":
            return DashScopeVideoProvider(**kwargs)
        return OpenAIVideoProvider(**kwargs)
    finally:
        del api_key


def resolve_video_provider(
    db,  # noqa: ANN001
    yard_id: str | None,
    *,
    agent_code: str | None = None,
    bound_supplier_id: str | None = None,
) -> tuple[VideosProvider, str] | None:
    """沿绑定链找**视频类**供应商/模型；一个都没有时返回 None（调用方如实告知）。"""
    from sqlalchemy import or_

    from app.modules.llm import binding_service
    from app.modules.llm.supplier_models import Supplier, SupplierModel

    chain = binding_service.resolve_chain(
        db,
        yard_id,
        purpose="video_generation",
        agent_code=agent_code,
        bound_supplier_id=bound_supplier_id,
    )
    seen: set[str] = set()
    for candidate in chain:
        row = candidate.supplier
        model_row = candidate.supplier_model
        model_protocol = str(getattr(model_row, "protocol", "") or "")
        is_video = (
            getattr(row, "provider_type", "") in VIDEO_PROVIDER_TYPES
            or model_protocol in VIDEO_PROVIDER_TYPES
            or str(getattr(model_row, "modality", "") or "").lower() == "video"
        )
        if not is_video or row.id in seen:
            continue
        seen.add(row.id)
        return (
            build_video_provider(row, model_override=candidate.model_name, model_row=model_row),
            candidate.level,
        )

    model_row = (
        db.query(SupplierModel)
        .join(Supplier, Supplier.id == SupplierModel.supplier_id)
        .filter(
            Supplier.status == 1,
            SupplierModel.status == 1,
            or_(
                SupplierModel.protocol.in_(sorted(VIDEO_PROVIDER_TYPES)),
                SupplierModel.modality == "video",
            ),
            or_(Supplier.yard_id == yard_id, Supplier.yard_id.is_(None)),
        )
        .order_by(Supplier.priority.desc(), SupplierModel.created_at.asc())
        .first()
    )
    if model_row is None:
        return None
    supplier = db.get(Supplier, model_row.supplier_id)
    if supplier is None:  # pragma: no cover - FK 保证存在
        return None
    return build_video_provider(supplier, model_row=model_row), "video_model_priority"


__all__ = [
    "DEFAULT_DURATION_SECONDS",
    "DEFAULT_SIZE",
    "MAX_DURATION_SECONDS",
    "VIDEO_PROVIDER_LABELS",
    "VIDEO_PROVIDER_TYPES",
    "DashScopeVideoProvider",
    "OpenAIVideoProvider",
    "VideoAsset",
    "VideoBatch",
    "VideoRequest",
    "VideosProvider",
    "build_video_provider",
    "resolve_video_provider",
    "video_error",
]
