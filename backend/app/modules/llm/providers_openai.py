"""OpenAI 兼容协议 Provider。

对接任意兼容 POST {base_url}/chat/completions 的服务。
密钥只出现在 Authorization 头中，绝不打日志、绝不进错误 details。
"""

import time

import httpx

from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.llm.schemas import LLMRequest, LLMResponse

log = get_logger(__name__)

# 简单成本单价（美元/千 token），按 input+output 合计估算
PRICE_PER_1K_USD = 0.002


def _llm_error(category: str, message: str) -> AppError:
    return AppError("LLM_ERROR", message, http_status=502, details={"category": category})


class OpenAICompatibleProvider:
    PRICE_PER_1K_USD = PRICE_PER_1K_USD
    #: 调用记录标签（llm_calls.provider，最长 50）；由供应商体系构建时注入
    provider_name: str | None = None
    #: 所属供应商行 ID；由供应商体系构建时注入，用于调用成本归属
    supplier_id: str | None = None

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: int | None = None,
    ):
        self.base_url = (base_url or settings.llm_base_url or "").rstrip("/")
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_model
        self.timeout = timeout_seconds or settings.llm_timeout_seconds
        if not self.base_url or not self.api_key:
            raise AppError(
                "LLM_NOT_CONFIGURED",
                "真实模型未配置：缺少 base_url 或 api_key，请联系管理员。",
                http_status=503,
            )

    async def complete(self, request: LLMRequest) -> LLMResponse:
        model = request.model or self.model
        payload: dict = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            payload["temperature"] = request.temperature

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
        except httpx.TimeoutException as e:
            raise _llm_error("timeout", f"模型请求超时（{self.timeout} 秒），请稍后重试。") from e
        except httpx.ConnectError as e:
            raise _llm_error("connection", "无法连接模型服务，请检查网络与服务地址。") from e
        except httpx.HTTPError as e:
            raise _llm_error("connection", "模型请求网络异常，请稍后重试。") from e

        if resp.status_code in (401, 403):
            raise _llm_error("auth", "模型服务鉴权失败，请检查密钥配置。")
        if resp.status_code == 429:
            raise _llm_error("rate_limit", "模型服务限流，请稍后重试。")
        if 500 <= resp.status_code < 600:
            raise _llm_error("provider_5xx", "模型服务暂时不可用，请稍后重试。")
        if resp.status_code != 200:
            raise _llm_error("provider_4xx", "模型服务返回异常，请稍后重试。")

        try:
            data = resp.json()
        except ValueError as e:
            raise _llm_error("provider_5xx", "模型服务返回无法解析，请稍后重试。") from e

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = data.get("usage") or {}
        latency_ms = int((time.perf_counter() - started) * 1000)
        return LLMResponse(
            content=message.get("content") or "",
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            model=data.get("model") or model,
            latency_ms=latency_ms,
            finish_reason=choice.get("finish_reason"),
        )
