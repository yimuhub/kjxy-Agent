"""增长域工具元数据落库（幂等）。

工具实现各自在自己的模块里注册与描述；这里只统一写 ``tool_definitions``，
供「可用工具」目录展示。新增增长域工具时在 ``_metas()`` 里追加一条即可。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session


def growth_tool_metas() -> list[dict[str, Any]]:
    from app.modules.growth.compliance import tool_meta as compliance_meta
    from app.modules.growth.poster import tool_meta as image_meta

    return [compliance_meta(), image_meta()]


def upsert_tool_meta(db: Session, meta: dict[str, Any]) -> int:
    """幂等写入单条工具元数据；返回新增条数（0=已存在并刷新）。"""
    from app.modules.tools.models import ToolDefinition

    row = db.query(ToolDefinition).filter_by(code=meta["code"]).first()
    if row is None:
        db.add(ToolDefinition(**meta))
        db.flush()
        return 1
    # 只刷新描述性字段，保留 enabled 开关与 source（用户在界面上的调整不被种子覆盖）
    for key in (
        "name",
        "description",
        "input_schema_json",
        "output_schema_json",
        "risk_level",
        "timeout_seconds",
    ):
        setattr(row, key, meta[key])
    db.flush()
    return 0


def seed_growth_tools(db: Session) -> int:
    return sum(upsert_tool_meta(db, meta) for meta in growth_tool_metas())


__all__ = ["growth_tool_metas", "seed_growth_tools", "upsert_tool_meta"]
