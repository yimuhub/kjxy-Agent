"""OpenAI 兼容协议 Provider。

对接任意兼容 POST {base_url}/chat/completions 的服务。
密钥只出现在 Authorization 头中，绝不打日志、绝不进错误 details。
"""

import time
from typing import Any

import httpx

from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.llm.schemas import LLMRequest, LLMResponse, ToolCall

log = get_logger(__name__)

# 简单成本单价（美元/千 token），按 input+output 合计估算
PRICE_PER_1K_USD = 0.002

#: default_params 里禁止下发的键：这些由平台/请求侧掌控，
#: 允许模型行覆盖会导致消息被替换、流式响应解析崩溃等不可预期行为。
_BLOCKED_DEFAULT_PARAMS = frozenset({"model", "messages", "tools", "tool_choice", "stream"})


def sanitize_default_params(params: Any) -> dict[str, Any]:
    """过滤模型行 ``default_params``：只保留可安全下发的键值。"""
    if not isinstance(params, dict):
        return {}
    return {
        str(key): value
        for key, value in params.items()
        if str(key) not in _BLOCKED_DEFAULT_PARAMS and value is not None
    }


def _llm_error(category: str, message: str) -> AppError:
    return AppError("LLM_ERROR", message, http_status=502, details={"category": category})


def _message_payload(m) -> dict:
    """ChatMessage → OpenAI chat messages 帧（含工具循环扩展字段，按需出现）。"""
    payload: dict = {"role": m.role, "content": m.content}
    if m.tool_call_id:
        payload["tool_call_id"] = m.tool_call_id
    if m.tool_calls:
        payload["tool_calls"] = m.tool_calls
    return payload


def _parse_tool_calls(message: dict) -> list[ToolCall] | None:
    """choices[].message.tool_calls → 结构化 ToolCall 列表（无则 None）。

    字段缺失/形态异常一律宽容降级：arguments 缺失按 ``"{}"``（交给工具层的
    Schema 校验失败回填，provider 不猜模型意图）；非法条目直接跳过。
    """
    raw = message.get("tool_calls")
    if not isinstance(raw, list) or not raw:
        return None
    out: list[ToolCall] = []
    for i, tc in enumerate(raw):
        if not isinstance(tc, dict):
            continue
        raw_fn = tc.get("function")
        fn = raw_fn if isinstance(raw_fn, dict) else {}
        name = str(fn.get("name") or "")
        if not name:
            continue
        args = fn.get("arguments")
        out.append(
            ToolCall(
                id=str(tc.get("id") or f"call_{i}"),
                name=name,
                args_json=args if isinstance(args, str) and args else "{}",
            )
        )
    return out or None


class OpenAICompatibleProvider:
    PRICE_PER_1K_USD = PRICE_PER_1K_USD
    #: 调用记录标签（llm_calls.provider，最长 50）；由供应商体系构建时注入
    provider_name: str | None = None
    #: 所属供应商行 ID；由供应商体系构建时注入，用于调用成本归属
    supplier_id: str | None = None
    #: function-calling 能力位：供应商配置显式声明后才走工具循环（默认关闭，零回归）
    supports_tools: bool = False
    #: 供应商单价（元/千 token）；未填则回落到 PRICE_PER_1K_USD 估算
    price_in_per_1k: float | None = None
    price_out_per_1k: float | None = None
    #: 模型行 ``supplier_models.default_params``：该模型的默认请求参数
    #: （如 temperature/top_p/response_format）。此前该字段入库后无人消费，
    #: 属于"配置写进去不生效"的死配置；现由本类在构造 payload 时合并。
    default_params: dict[str, Any] | None = None

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
        # SSRF 出网校验（规范 §15.1）：模型端点不得指向回环/link-local/保留地址；
        # 私有网段按 enforce_model_egress_guard 策略执行（内网私有化部署默认放行）
        from app.core.net_guard import check_egress_url

        check_egress_url(f"{self.base_url}/chat/completions", context="llm_chat", allow_local=True)
        model = request.model or self.model
        # 默认参数合并顺序（后者覆盖前者）：
        #   1) 模型行 default_params（管理员在「模型中心」配的该模型默认参数）
        #   2) 请求级 extra_params（调用方本次调用的临时覆盖）
        #   3) 平台核心字段（model/messages/max_tokens/temperature/tools）—— 永不被覆盖
        merged_defaults = sanitize_default_params(self.default_params)
        merged_defaults.update(sanitize_default_params(request.extra_params))
        payload: dict = {
            **merged_defaults,
            "model": model,
            "messages": [_message_payload(m) for m in request.messages],
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        # function-calling：仅在调用方显式携带工具定义时透传（旧路径 payload 不变）
        if request.tools:
            payload["tools"] = request.tools

        started = time.perf_counter()
        # 长产出可以按请求放宽超时；缺省沿用供应商/全局配置
        timeout = request.timeout_seconds or self.timeout
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
        except httpx.TimeoutException as e:
            raise _llm_error("timeout", f"模型请求超时（{timeout} 秒），请稍后重试。") from e
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
            tool_calls=_parse_tool_calls(message),
        )
