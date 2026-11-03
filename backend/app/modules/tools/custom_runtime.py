"""自定义工具运行时（v3.25）：把数据库里的工具定义变成可执行 ToolDef。

两种方式，都不落任何密钥明文：
- ``prompt``：复用小院已配置的模型（走 ``complete_with_logging``，与业务同一套解析/兜底/记账），
  提示词模板用 ``{参数名}`` 引用入参；
- ``http``：按定义出网调用 JSON 接口（出网受 net_guard 管控、响应体与状态码有硬上限），
  头值密文存储、用时解密。

风险纪律沿用工具注册表既有闸门：参数 schema 校验、权限校验、
``external_side_effect`` 未批即建审批单——本模块只负责「怎么执行」，不负责放行。
"""

import asyncio
import json
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.llm.schemas import ChatMessage, LLMMetadata, LLMRequest
from app.modules.llm.service import complete_with_logging
from app.modules.tools.models import ToolDefinition
from app.modules.tools.registry import ToolDef

log = get_logger(__name__)

#: 自定义工具的模型用途码（同时登记在能力清单，可在模型中心单独绑供应商）
PURPOSE_TOOL_RUN = "tool_run"
#: HTTP 响应体上限（防内存与提示词注入放大）
HTTP_MAX_BYTES = 200 * 1024
HTTP_ALLOWED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _render(template: str, params: dict[str, Any]) -> str:
    """``{参数名}`` 直替（不用 format/eval：未知占位符原样留着，绝不抛 KeyError）。"""
    text = template
    for key, value in params.items():
        rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        text = text.replace("{" + str(key) + "}", rendered)
    return text


async def _run_prompt(
    db: Session, row: ToolDefinition, params: dict[str, Any], ctx: YardContext
) -> dict:
    impl = row.impl_json or {}
    template = str(impl.get("prompt_template") or "").strip()
    if not template:
        raise AppError(
            "TOOL_SPEC_INVALID",
            f"自定义工具 {row.code} 缺少提示词模板，请补全后再启用。",
            http_status=400,
        )
    resp = await complete_with_logging(
        db,
        LLMRequest(
            messages=[ChatMessage(role="user", content=_render(template, params))],
            timeout_seconds=row.timeout_seconds or 60,
            metadata=LLMMetadata(
                yard_id=ctx.yard_id,
                user_id=ctx.user_id,
                session_id=getattr(ctx, "session_id", None),
                request_id=ctx.request_id,
                purpose=PURPOSE_TOOL_RUN,
                agent_ref=row.code,
            ),
        ),
    )
    return {"text": resp.content or "", "model": resp.model, "tool": row.code}


def _http_call_sync(row: ToolDefinition, params: dict[str, Any]) -> dict:
    """纯网络一轮（不碰 DB），由线程池调用，避免阻塞事件循环。"""
    import contextlib

    import httpx

    from app.core.net_guard import resolve_egress_ips
    from app.modules.llm.supplier_crypto import decrypt_api_key
    from app.modules.search.egress import pin_dns

    impl = row.impl_json or {}
    url = str(impl.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise AppError("TOOL_SPEC_INVALID", f"自定义工具 {row.code} 的 URL 非法。", http_status=400)
    method = str(impl.get("method") or "POST").upper()
    if method not in HTTP_ALLOWED_METHODS:
        raise AppError("TOOL_SPEC_INVALID", f"不支持的 HTTP 方法：{method}。", http_status=400)
    # 校验与建连之间可能被 DNS 重绑定换答案：把解析结果钉住，答案变了就失败关闭
    verified = resolve_egress_ips(url, context=f"custom_tool:{row.code}")
    pinned = pin_dns(*verified) if verified else contextlib.nullcontext()

    headers: dict[str, str] = {}
    for name, cipher in (impl.get("headers_enc") or {}).items():
        headers[str(name)] = decrypt_api_key(str(cipher))

    try:
        with pinned:
            # trust_env=False：环境变量代理会让连接绕过 DNS 钉与出网校验
            with httpx.Client(timeout=row.timeout_seconds or 20, trust_env=False) as client:
                request_kwargs: dict[str, Any] = {"headers": headers}
                if method == "GET":
                    request_kwargs["params"] = params
                else:
                    request_kwargs["json"] = params
                resp = client.request(method, url, **request_kwargs)
    except httpx.TimeoutException as e:
        raise AppError(
            "TOOL_ERROR", f"工具请求超时（{row.timeout_seconds or 20} 秒）。", http_status=504
        ) from e
    except httpx.HTTPError as e:
        raise AppError("TOOL_ERROR", "工具外部接口不可达。", http_status=502) from e

    if len(resp.content) > HTTP_MAX_BYTES:
        raise AppError("TOOL_ERROR", "外部接口返回内容过大，已中止。", http_status=502)
    if resp.status_code >= 400:
        raise AppError(
            "TOOL_ERROR", f"外部接口返回异常状态码 {resp.status_code}。", http_status=502
        )
    try:
        data = resp.json()
    except ValueError:
        return {"text": (resp.text or "")[:2000], "tool": row.code, "content_type": "text/plain"}
    return {
        "text": json.dumps(data, ensure_ascii=False)[:4000],
        "data": data if isinstance(data, dict) else {"value": data},
        "tool": row.code,
        "content_type": "application/json",
    }


def build_custom_tool(row: ToolDefinition, db: Session) -> ToolDef | None:
    """DB 自定义行 → ToolDef（未启用/非 custom 来源返回 None）。

    提示：``db`` 在闭包里绑定，执行期不再按 code 重新取行——定义即本次调用的契约。
    """
    if (row.source or "builtin") != "custom" or not row.enabled:
        return None
    kind = row.impl_kind or "prompt"

    async def _func(params: dict, ctx: YardContext) -> dict:
        if kind == "prompt":
            return await _run_prompt(db, row, dict(params), ctx)
        if kind == "http":
            return await asyncio.to_thread(_http_call_sync, row, dict(params))
        raise AppError("TOOL_SPEC_INVALID", f"未知执行方式：{kind}。", http_status=400)

    return ToolDef(
        code=row.code,
        version=row.version,
        name=row.name,
        description=row.description,
        input_schema=row.input_schema_json or {"type": "object", "additionalProperties": False},
        func=_func,
        required_permissions=list(row.required_permissions_json or []),
        risk_level=row.risk_level,
        side_effect=bool(row.side_effect),
        timeout_seconds=row.timeout_seconds or 30,
        enabled=True,
    )
