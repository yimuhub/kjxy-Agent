"""请求上下文中间件（纯 ASGI 实现）。

职责：request_id 透传/生成 + contextvar 注入 + HTTP 指标计数。
使用纯 ASGI 而非 BaseHTTPMiddleware：避免后者的任务组封装在异常路径上的兼容性问题
（历史版本曾出现同一 requestData 双执行），并在响应头回写 X-Request-ID。
"""

import time
import uuid
from contextlib import suppress

from app.core.context import metrics, request_id_var
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_HEADER = b"x-request-id"


class RequestIDMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        incoming = headers.get(_HEADER)
        request_id = (
            incoming.decode("latin-1") if incoming else ""
        ) or f"req_{uuid.uuid4().hex[:20]}"
        request_id_var.set(request_id)
        scope.setdefault("state", {})["request_id"] = request_id
        status_holder = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = int(message["status"])
                message["headers"] = list(message.get("headers") or []) + [
                    (_HEADER, request_id.encode("latin-1"))
                ]
            await send(message)

        start = time.perf_counter()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.perf_counter() - start
            route = "unmatched"
            with suppress(Exception):
                # Starlette 路由匹配后会把 Route 对象写回 scope
                route = getattr(scope.get("route"), "path", None) or "unmatched"
            metrics.observe_http(
                route=route,
                method=str(scope.get("method", "-")),
                status=status_holder["status"],
                seconds=duration,
            )
