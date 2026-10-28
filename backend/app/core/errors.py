"""统一错误模型。错误码见 contracts/error-codes.md。"""

from fastapi import Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    def __init__(
        self, code: str, message: str, http_status: int = 400, details: dict | None = None
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details or {}


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "req_unknown")


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Starlette 的 add_exception_handler 要求 handler 参数为 Exception（逆变），
    # 注册类为 AppError 时实际收到的必然是 AppError 实例。
    if isinstance(exc, AppError):
        code, message, http_status, details = exc.code, exc.message, exc.http_status, exc.details
    else:  # pragma: no cover - 防御分支
        code, message, http_status, details = "INTERNAL_ERROR", "服务暂时不可用。", 500, {}
    return JSONResponse(
        status_code=http_status,
        content={
            # details 可能携带资源快照（含 datetime 等），统一编码后再输出
            "error": {"code": code, "message": message, "details": jsonable_encoder(details)},
            "request_id": _request_id(request),
        },
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, RequestValidationError):
        details = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]}
            for e in exc.errors()
        ]
    else:  # pragma: no cover - 防御分支
        details = []
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "error": {
                "code": "INVALID_REQUEST",
                "message": "请求参数有误，请检查后重试。",
                "details": {"fields": details},
            },
            "request_id": _request_id(request),
        },
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "服务暂时不可用，请稍后重试。",
                "details": {},
            },
            "request_id": _request_id(request),
        },
    )


# HTTP 状态码 -> 统一错误码：框架自带的 404/405/403 等也走同一套信封，
# 否则前端只能拿到 {"detail": ...}，会被当成「网络连接失败」误报。
_HTTP_STATUS_CODES = {
    400: "VALIDATION_ERROR",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
}


async def http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, StarletteHTTPException):
        http_status = exc.status_code
        detail = exc.detail
    else:  # pragma: no cover - 防御分支
        http_status, detail = 500, "服务暂时不可用，请稍后重试。"
    code = _HTTP_STATUS_CODES.get(
        http_status, "INTERNAL_ERROR" if http_status >= 500 else "UNKNOWN"
    )
    message = detail if isinstance(detail, str) and detail else ""
    return JSONResponse(
        status_code=http_status,
        content={
            "error": {"code": code, "message": message, "details": {}},
            "request_id": _request_id(request),
        },
    )
