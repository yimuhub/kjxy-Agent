"""联网搜索工具：web.search / web.fetch。

按知识库模块的同款方式向全局工具注册表注册实现；**不在 import 时注册**，
由主程序启动阶段显式调用 ``register_search_tools()``（可重复调用）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.search.egress import MAX_CHARS_LIMIT, fetch_public_url
from app.modules.search.providers import MAX_RESULTS
from app.modules.search.service import search_web

log = get_logger(__name__)

WEB_SEARCH_VERSION = "1.0.0"
WEB_FETCH_VERSION = "1.0.0"

WEB_SEARCH_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "maxLength": 500,
            "description": "搜索关键词",
        },
        "count": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_RESULTS,
            "default": 5,
            "description": "返回条数（最多 8）",
        },
        "lang": {
            "type": "string",
            "default": "zh-CN",
            "description": "结果语言，如 zh-CN / en",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}

WEB_SEARCH_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "provider": {"type": "string"},
        "results": {"type": "array", "items": {"type": "object"}},
        "sources": {"type": "array", "items": {"type": "object"}},
        "fetched_at": {"type": "string"},
        "cached": {"type": "boolean"},
    },
    "required": ["query", "provider", "results", "sources", "fetched_at", "cached"],
}

WEB_FETCH_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "minLength": 1,
            "maxLength": 2048,
            "description": "公网 HTTP(S) 网页地址（禁止内网）",
        },
        "max_chars": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_CHARS_LIMIT,
            "default": MAX_CHARS_LIMIT,
            "description": "正文最大字符数（最多 6000）",
        },
    },
    "required": ["url"],
    "additionalProperties": False,
}

WEB_FETCH_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "text": {"type": "string"},
        "url": {"type": "string"},
        "content_type": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["title", "text", "url", "content_type", "sources"],
}


def _require_yard(ctx: YardContext) -> str:
    yard_id = getattr(ctx, "yard_id", "") or ""
    if not yard_id:
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    return yard_id


def _session() -> Session:
    from app.db.session import SessionLocal

    return SessionLocal()


async def _web_search_tool_func(params: dict, ctx: YardContext) -> dict[str, Any]:
    yard_id = _require_yard(ctx)
    db = _session()
    try:
        data = await search_web(
            db,
            yard_id,
            params.get("query") or "",
            count=int(params.get("count") or 5),
            lang=params.get("lang") or "zh-CN",
        )
    finally:
        db.close()
    sources = [
        {
            "title": row["title"],
            "url": row["url"],
            "source": row["source"],
            "published": row.get("published"),
        }
        for row in data["results"]
    ]
    return {**data, "sources": sources}


async def _web_fetch_tool_func(params: dict, ctx: YardContext) -> dict[str, Any]:
    _require_yard(ctx)
    page = await fetch_public_url(
        params.get("url") or "",
        max_chars=int(params.get("max_chars") or MAX_CHARS_LIMIT),
    )
    return {
        **page,
        "sources": [
            {
                "title": page["title"] or page["url"],
                "url": page["url"],
                "source": page["url"],
                "published": None,
            }
        ],
    }


def register_search_tools() -> None:
    """注册 web.search / web.fetch；重复调用不会重复注册。"""
    try:
        from app.modules.tools.registry import ToolDef, tool_registry
    except Exception as e:  # pragma: no cover - 注册表不可用时静默降级
        log.warning("工具注册表不可用，联网搜索工具未注册: %s", e)
        return

    specs = [
        ToolDef(
            code="web.search",
            version=WEB_SEARCH_VERSION,
            name="联网搜索",
            description="联网搜索，返回来源（标题/链接/摘要）；用于获取公开网络信息。",
            input_schema=WEB_SEARCH_INPUT_SCHEMA,
            output_schema=WEB_SEARCH_OUTPUT_SCHEMA,
            func=_web_search_tool_func,
            required_permissions=["tools:execute"],
            risk_level="read_only",
            side_effect=False,
            timeout_seconds=30,
        ),
        ToolDef(
            code="web.fetch",
            version=WEB_FETCH_VERSION,
            name="网页正文抓取",
            description="抓取公网网页正文，禁止内网与云元数据地址。",
            input_schema=WEB_FETCH_INPUT_SCHEMA,
            output_schema=WEB_FETCH_OUTPUT_SCHEMA,
            func=_web_fetch_tool_func,
            required_permissions=["tools:execute"],
            risk_level="read_only",
            side_effect=False,
            timeout_seconds=20,
        ),
    ]
    for spec in specs:
        try:
            tool_registry.get(spec.code)
            continue
        except AppError:
            pass
        except Exception:  # pragma: no cover - 防御
            continue
        tool_registry.register(spec)
        log.info("%s 工具已注册", spec.code)


__all__ = [
    "WEB_FETCH_INPUT_SCHEMA",
    "WEB_SEARCH_INPUT_SCHEMA",
    "register_search_tools",
]
