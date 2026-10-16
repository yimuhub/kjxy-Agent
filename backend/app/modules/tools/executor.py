"""工具执行器桥接。

agents.OrchestratorAgent 的默认工具执行器约定为 ``execute_tool(name, params)``，
而 ToolRegistry.execute 需要 (db, code, params, ctx, request_id)。
本模块提供：

- ``make_tool_executor(db, ctx, request_id)``：为一次执行绑定上下文的工厂；
- ``bind_tool_context(db, ctx, request_id)`` + 模块级 ``execute_tool(name, params)``：
  基于 contextvars 的隐式绑定，供 orchestrator 默认路径使用。
"""

from contextvars import ContextVar
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import TenantContext
from app.core.errors import AppError
from app.modules.tools.registry import tool_registry

_bound: ContextVar[tuple[Session, TenantContext, str] | None] = ContextVar(
    "tool_exec_ctx", default=None
)


def bind_tool_context(db: Session, ctx: TenantContext, request_id: str):
    """绑定当前任务的工具执行上下文，返回 token（调用方负责 reset）。"""
    return _bound.set((db, ctx, request_id))


def make_tool_executor(db: Session, ctx: TenantContext, request_id: str):
    """创建绑定了 (db, ctx, request_id) 的 (name, params) 执行器。"""

    async def execute_tool(name: str, params: dict) -> dict:
        return await tool_registry.execute(db, name, params, ctx, request_id)

    return execute_tool


async def execute_tool(name: str, params: dict) -> dict[str, Any]:
    """orchestrator 默认执行器：使用 contextvars 绑定的上下文。"""
    bound = _bound.get()
    if bound is None:
        raise AppError("TOOL_NOT_FOUND", f"工具执行上下文未绑定：{name}。", http_status=500)
    db, ctx, request_id = bound
    return await tool_registry.execute(db, name, params or {}, ctx, request_id)
