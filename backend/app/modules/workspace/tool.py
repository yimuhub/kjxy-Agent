"""``workspace.edit_file`` 工具：让 Agent 在受控工作区内改代码。

- 一次调用 = 预览 + 应用，前端仍可回滚（变更台账保留 before/after 快照）；
- risk_level=external_side_effect：默认须人工审批；
- 仍受项目允许路径、敏感文件、1MB 上限与插件授权约束。
"""

from __future__ import annotations

from typing import Any

from app.core.deps import YardContext
from app.core.logging import get_logger

log = get_logger(__name__)

TOOL_CODE = "workspace.edit_file"

INPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "project_id": {"type": "string", "minLength": 1, "maxLength": 36},
        "plugin_id": {"type": "string", "minLength": 1, "maxLength": 120},
        "path": {"type": "string", "minLength": 1, "maxLength": 1024},
        "content": {"type": "string"},
    },
    "required": ["project_id", "plugin_id", "path", "content"],
    "additionalProperties": False,
}


def _run(params: dict, ctx: YardContext) -> dict[str, Any]:
    from app.db.session import SessionLocal
    from app.modules.workspace import service

    db = SessionLocal()
    try:
        change = service.preview_change(
            db,
            ctx,
            project_id=str(params["project_id"]),
            plugin_id=str(params["plugin_id"]),
            path=str(params["path"]),
            content=str(params["content"]),
        )
        applied = service.apply_change(db, ctx, str(change["id"]))
        db.commit()
        return {
            "change_id": applied["id"],
            "project_id": applied["project_id"],
            "path": applied["path"],
            "added_lines": applied["added_lines"],
            "removed_lines": applied["removed_lines"],
            "diff": applied["diff"],
            "status": applied["status"],
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def register_workspace_tool():
    try:
        from app.modules.tools.registry import ToolDef, tool_registry
    except Exception as e:
        log.warning("工具注册表不可用，workspace.edit_file 未注册: %s", e)
        return None
    tool_def = ToolDef(
        code=TOOL_CODE,
        version="1.0.0",
        name="工作区改文件（受控）",
        description=(
            "在管理员登记的工作区内修改一个文本文件。仅允许项目根内的相对路径，"
            "敏感文件与二进制一律拒绝；应用后可在工作区页面回滚。"
        ),
        input_schema=INPUT_SCHEMA,
        required_permissions=["tools:execute", "workspace:write"],
        risk_level="external_side_effect",
        side_effect=True,
        timeout_seconds=30,
        func=_run,
    )
    try:
        tool_registry.register(tool_def)
    except Exception as e:
        log.warning("workspace.edit_file 注册失败: %s", e)
        return None
    log.info("workspace.edit_file 工具已注册")
    return tool_def
