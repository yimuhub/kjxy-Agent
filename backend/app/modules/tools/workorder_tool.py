"""高风险演示工具：作业工单下发（dry-run）。

- risk_level=external_side_effect：调用必经人工审批（见 approvals.py）。
- 实现为 dry-run：只校验参数并返回"拟下发"记录，不产生真实外部副作用。
  生产接入真实工单系统时，替换 _workorder_dispatch_func 内部实现即可，
  审批流与任务状态机无需改动。
- 同时注册任务处理器 ``workorder.dispatch``，供任务链路演示
  发起→awaiting_review→审批→执行的完整闭环。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.deps import TenantContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.tools.models import ToolApproval

log = get_logger(__name__)

TOOL_CODE = "workorder.dispatch"
TASK_TYPE = "workorder.dispatch"

WORKORDER_INPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 200, "description": "工单标题"},
        "description": {"type": "string", "maxLength": 2000, "description": "工单内容描述"},
        "priority": {
            "type": "string",
            "enum": ["low", "normal", "high", "urgent"],
            "default": "normal",
        },
        "assignee": {"type": "string", "maxLength": 100, "description": "指派人"},
    },
    "required": ["title"],
    "additionalProperties": False,
}


def _workorder_dispatch_func(params: dict, ctx: TenantContext) -> dict:
    """dry-run 实现：不做真实下发，返回拟下发记录。"""
    if not ctx.tenant_id:
        raise AppError("TENANT_REQUIRED", "该操作需要租户上下文。", http_status=400)
    workorder = {
        "title": params["title"],
        "description": params.get("description", ""),
        "priority": params.get("priority", "normal"),
        "assignee": params.get("assignee", ""),
        "tenant_id": ctx.tenant_id,
        "requested_by": ctx.user_id,
    }
    log.info("workorder.dispatch dry-run: %s", workorder["title"])
    return {
        "dry_run": True,
        "dispatched": False,
        "workorder": workorder,
        "message": "演示模式：工单未真实下发，仅记录下发意图。审批通过后可重复执行。",
    }


def register_workorder_tool():
    """向全局工具注册表注册 workorder.dispatch（external_side_effect）。"""
    try:
        from app.modules.tools.registry import ToolDef, tool_registry
    except Exception as e:
        log.warning("工具注册表不可用，workorder.dispatch 未注册: %s", e)
        return None
    tool_def = ToolDef(
        code=TOOL_CODE,
        version="1.0.0",
        name="作业工单下发（演示）",
        description="高风险演示工具：下发作业工单，须经人工审批。当前为 dry-run 实现。",
        input_schema=WORKORDER_INPUT_SCHEMA,
        required_permissions=["tools:execute"],
        risk_level="external_side_effect",
        side_effect=True,
        timeout_seconds=30,
        func=_workorder_dispatch_func,
    )
    try:
        tool_registry.register(tool_def)
    except Exception as e:
        log.warning("workorder.dispatch 注册失败: %s", e)
        return None
    log.info("workorder.dispatch 工具已注册")
    return tool_def


async def _execute_workorder(
    db: Any, params: dict[str, Any], ctx: TenantContext, request_id: str
) -> dict[str, Any]:
    from app.modules.tools.registry import tool_registry

    return await tool_registry.execute(db, TOOL_CODE, params, ctx, request_id)


def workorder_dispatch_handler(
    input_data: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    """tasks 处理器：调用 workorder.dispatch 工具。

    工具被风险拦截（TOOL_RISK_BLOCKED）时：把审批单与任务关联后提交，
    重新抛出，由 worker 将任务置为 awaiting_review（见 worker_tasks）。
    """
    from app.core.config import settings
    from app.core.deps import TenantContext
    from app.modules.auth.models import User
    from app.modules.tasks.worker_tasks import _get_session_factory
    from app.modules.tenants import repository as tenant_repo

    db = _get_session_factory()()
    try:
        tenant_id = str(context.get("tenant_id") or "")
        actor_id = str(context.get("actor_id") or "")
        # 解析执行人角色（与登录时一致：租户成员角色 + 平台管理员补齐）
        roles: list[str] = []
        membership = tenant_repo.get_membership(db, tenant_id, actor_id)
        if membership is not None:
            roles = [membership.role]
        user = db.get(User, actor_id)
        if (
            user is not None
            and user.username in settings.platform_admin_username_list
            and "platform_admin" not in roles
        ):
            roles = ["platform_admin", *roles]
        ctx = TenantContext(
            tenant_id=tenant_id,
            user_id=actor_id,
            roles=roles,
            request_id=str(context.get("request_id") or "req_unknown"),
        )
        result = asyncio.run(_execute_workorder(db, dict(input_data or {}), ctx, ctx.request_id))
        db.commit()
        return result
    except AppError as e:
        # 审批单已在 registry.execute 内创建；此处关联任务后落库，再上抛
        if e.code == "TOOL_RISK_BLOCKED":
            approval_id = (e.details or {}).get("approval_id")
            if approval_id:
                approval = db.get(ToolApproval, approval_id)
                if approval is not None and not approval.task_id:
                    approval.task_id = str(context.get("task_id") or "")
            db.commit()
        else:
            db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# 注册任务处理器（tasks 模块约定）
try:
    from app.modules.tasks.handlers import register_handler

    register_handler(TASK_TYPE)(workorder_dispatch_handler)
    log.info("task handler registered: %s", TASK_TYPE)
except ImportError as e:
    log.warning("tasks.handlers unavailable, workorder handler not registered: %s", e)
