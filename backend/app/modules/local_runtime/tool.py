"""``computer.use`` 工具：让 Agent 通过本机桥完成界面操作。

- risk_level=external_side_effect：调用必经人工审批（与工单下发同闸门）；
- 会话「完全控制」档可免审批直接执行，但仍受插件权限声明与授权台账约束；
- 工具本身不记录截图/输入文本，审计只写动作与结局。
"""

from __future__ import annotations

from typing import Any

from app.core.deps import YardContext
from app.core.logging import get_logger
from app.modules.local_runtime.actions import ACTION_CATALOG

log = get_logger(__name__)

TOOL_CODE = "computer.use"

INPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "plugin_id": {"type": "string", "minLength": 1, "maxLength": 120},
        "action": {"type": "string", "enum": sorted(ACTION_CATALOG)},
        "params": {"type": "object", "default": {}},
    },
    "required": ["plugin_id", "action"],
    "additionalProperties": False,
}


def _run(params: dict, ctx: YardContext) -> dict[str, Any]:
    from app.db.session import SessionLocal
    from app.modules.local_runtime import service

    db = SessionLocal()
    try:
        result = service.execute(
            db,
            ctx,
            plugin_id=str(params["plugin_id"]),
            action=str(params["action"]),
            params=params.get("params") or {},
        )
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def register_computer_use_tool():
    try:
        from app.modules.tools.registry import ToolDef, tool_registry
    except Exception as e:
        log.warning("工具注册表不可用，computer.use 未注册: %s", e)
        return None
    tool_def = ToolDef(
        code=TOOL_CODE,
        version="1.0.0",
        name="电脑操作（本机能力插件）",
        description=(
            "通过已授权并启用的本机能力插件操作这台电脑：截屏、列窗口、点击、输入、"
            "按键、剪贴板与打开应用。属于高风险外部动作，须经人工审批。"
        ),
        input_schema=INPUT_SCHEMA,
        required_permissions=["tools:execute", "runtime:use"],
        risk_level="external_side_effect",
        side_effect=True,
        timeout_seconds=30,
        func=_run,
    )
    try:
        tool_registry.register(tool_def)
    except Exception as e:
        log.warning("computer.use 注册失败: %s", e)
        return None
    log.info("computer.use 工具已注册")
    return tool_def
