"""「一句话建助手」工具化（v3.51）。

把 ``agents/agent_builder`` 的生成链路注册成 ``agent.generate`` 工具，指挥官亲自
执行时就能「团队缺人就地造人」，不必退出对话再点向导——这是用户要求挂在指挥官
身上的能力之一（自建 Agent）。

边界与 HTTP 端点 ``POST /agents/generate`` 完全同口径：一律落**草稿**（不自动发布，
ADR-0005 红线）、受本小院开关与每日上限约束、需要 ``agents:manage`` 权限；工具层
不提供任何绕过审核的入口。执行函数自开数据库会话（与 web.search / skill.* 同口径）。
"""

from __future__ import annotations

from app.core.deps import YardContext
from app.core.errors import AppError


def _db_session():
    from app.db.session import SessionLocal

    return SessionLocal()


async def _agent_generate_func(params: dict, ctx: YardContext) -> dict:
    """agent.generate：一句话生成智能体草稿，返回可读摘要供后续派单复用。"""
    from app.modules.agents import agent_builder

    utterance = str((params or {}).get("utterance") or "").strip()
    if len(utterance) < 4:
        raise AppError(
            "TOOL_VALIDATION_FAILED",
            "请提供 utterance（一句话说清需要什么样的助手）。",
            http_status=400,
        )
    visibility = str((params or {}).get("visibility") or "public").strip() or "public"
    if visibility not in ("public", "private"):
        raise AppError(
            "TOOL_VALIDATION_FAILED", "visibility 只能是 public 或 private。", http_status=400
        )
    use_web = bool((params or {}).get("use_web", True))
    db = _db_session()
    try:
        result = await agent_builder.generate_agent(
            db, ctx=ctx, utterance=utterance[:800], visibility=visibility, use_web=use_web
        )
        db.commit()
    finally:
        db.close()
    agent = result.get("agent") if isinstance(result, dict) else None
    agent = agent if isinstance(agent, dict) else {}
    requirements = result.get("skill_requirements") or []
    return {
        "agent_id": str(agent.get("id") or ""),
        "agent_code": str(agent.get("agent_code") or ""),
        "name": str(agent.get("name") or ""),
        "status": str(agent.get("status") or "draft"),
        "visibility": str(agent.get("visibility") or visibility),
        "description": str(agent.get("description") or ""),
        "bound_skills": list(agent.get("bound_skills") or []),
        "allowed_tools": list(agent.get("allowed_tools") or []),
        "matched_skills": len(result.get("matched") or []),
        # 草稿态还不能派单：让调用方（指挥官）与用户都知道下一步是「发布」
        "missing_skills": [
            str(item.get("need") or "") for item in requirements if isinstance(item, dict)
        ][:5],
        "offline": bool(result.get("offline")),
        "note": str(result.get("note") or ""),
        "next": "助手已存为草稿；小院负责人在「智能体」里发布后即可按 agent_code 派单。",
    }


def register_agent_tools() -> None:
    """注册 agent.generate（幂等：重复注册覆盖同一 code）。"""
    from app.modules.tools.registry import ToolDef, tool_registry

    tool_registry.register(
        ToolDef(
            code="agent.generate",
            version="1.0.0",
            name="生成智能体",
            description=(
                "按一句话需求创建一个新的领域助手（保存为草稿，需负责人发布后才可派单）；"
                "用于团队里确实没有能胜任某任务的助手时"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "utterance": {"type": "string", "minLength": 4, "maxLength": 800},
                    "visibility": {"type": "string", "enum": ["public", "private"]},
                    "use_web": {"type": "boolean", "default": True},
                },
                "required": ["utterance"],
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            required_permissions=["agents:manage"],
            risk_level="draft",
            side_effect=True,
            timeout_seconds=240,
            func=_agent_generate_func,
        )
    )


__all__ = ["register_agent_tools"]
