"""技能 Agent 工具化（v3.3）：把一句话技能链路注册为可调用工具。

Agent 在多轮对话中即可完成「检索 → 生成 → 安装」全流程，而不必退出对话：

- ``skill.search``   ：一句话检索现成技能（本地内置/缓存 → GitHub），只读；
- ``skill.generate`` ：按一句话需求生成技能私有草稿（``skills:generate``）；
- ``skill.install``  ：安装检索到的市场条目 / 把自己的草稿送审
  （``market:install`` + ``external_side_effect`` 高风险闸：安装前必须过
  人工审批，即「安装二次确认」）。

安全边界与 HTTP 端点同口径：生成一律落私有草稿、安装走市场既有安装闸，
工具层不提供任何绕过审核的入口。工具执行函数自开数据库会话（与
knowledge.search 同口径）。
"""

from __future__ import annotations

from typing import Any

from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.market import skill_agent


def _db_session():
    from app.db.session import SessionLocal

    return SessionLocal()


def _require_text(params: dict, field: str, message: str) -> str:
    text = str((params or {}).get(field) or "").strip()
    if not text:
        raise AppError("TOOL_VALIDATION_FAILED", message, http_status=400)
    return text


def _skill_search_func(params: dict, ctx: YardContext) -> dict:
    """skill.search：一句话检索现成技能。"""
    utterance = _require_text(params, "utterance", "请提供 utterance（你要的技能描述）。")
    db = _db_session()
    try:
        return skill_agent.request_skill(db, ctx=ctx, utterance=utterance)
    finally:
        db.close()


async def _skill_generate_func(params: dict, ctx: YardContext) -> dict:
    """skill.generate：一句话生成技能私有草稿（联网找资料 → 模型出规格）。"""
    utterance = _require_text(params, "utterance", "请提供 utterance（你要的技能描述）。")
    use_web = bool((params or {}).get("use_web", True))
    db = _db_session()
    try:
        return await skill_agent.generate_skill(db, ctx=ctx, utterance=utterance, use_web=use_web)
    finally:
        db.close()


def _skill_install_func(params: dict, ctx: YardContext) -> dict:
    """skill.install：安装市场技能条目，或把自己的草稿送审。

    安装第三方条目走市场既有安装闸（pending_review），不直接启用。
    """
    data: dict[str, Any] = dict(params or {})
    payload = data.get("payload")
    if isinstance(payload, dict):
        data = {**data, **payload}
    item_key = str(data.get("item_key") or "").strip()
    skill_id = str(data.get("skill_id") or "").strip()
    if not item_key and not skill_id:
        raise AppError(
            "TOOL_VALIDATION_FAILED",
            "请提供 item_key（市场条目）或 skill_id（自己的草稿）。",
            http_status=400,
        )
    db = _db_session()
    try:
        return skill_agent.install_skill(
            db, ctx=ctx, payload={"item_key": item_key, "skill_id": skill_id}
        )
    finally:
        db.close()


def register_skill_agent_tools() -> None:
    """注册 skill.search / skill.generate / skill.install 三个工具（幂等）。"""
    from app.modules.tools.registry import ToolDef, tool_registry

    tool_registry.register(
        ToolDef(
            code="skill.search",
            version="1.0.0",
            name="技能检索",
            description="按一句话需求检索现成技能（本地技能库优先，无命中再查开源市场）",
            input_schema={
                "type": "object",
                "properties": {
                    "utterance": {"type": "string", "minLength": 1, "maxLength": 1000},
                },
                "required": ["utterance"],
                "additionalProperties": False,
            },
            required_permissions=["skills:generate"],
            risk_level="read_only",
            side_effect=False,
            timeout_seconds=30,
            func=_skill_search_func,
        )
    )
    tool_registry.register(
        ToolDef(
            code="skill.generate",
            version="1.0.0",
            name="技能生成",
            description="按一句话需求生成技能草稿（私有，需审核后才能共享）",
            input_schema={
                "type": "object",
                "properties": {
                    "utterance": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "use_web": {"type": "boolean", "default": True},
                },
                "required": ["utterance"],
                "additionalProperties": False,
            },
            required_permissions=["skills:generate"],
            risk_level="draft",
            side_effect=True,
            timeout_seconds=180,
            func=_skill_generate_func,
        )
    )
    tool_registry.register(
        ToolDef(
            code="skill.install",
            version="1.0.0",
            name="技能安装",
            description=(
                "安装检索到的技能条目（第三方条目安装后待审），" "或把自己的技能草稿提交审核"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "item_key": {"type": "string", "maxLength": 200},
                    "skill_id": {"type": "string", "maxLength": 36},
                },
                "additionalProperties": False,
            },
            required_permissions=["market:install"],
            risk_level="external_side_effect",
            side_effect=True,
            timeout_seconds=60,
            func=_skill_install_func,
        )
    )


__all__ = ["register_skill_agent_tools"]
