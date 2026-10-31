"""锁定助手会话的文档生成链路测试（3.35）。

回归点：绑定助手后每轮直达该对象，历史上会绕过文档意图；现在文档需求
必须仍然生成文件，并带上助手人设与已绑定的办公技能。
"""

from __future__ import annotations

import asyncio

from app.core.deps import YardContext
from app.modules.agents.models import YardAgent
from app.modules.chat import document as document_service
from app.modules.chat import service
from app.modules.yards.repository import create_yard


def _ctx(yard_id: str) -> YardContext:
    return YardContext(
        yard_id=yard_id,
        user_id="u-office",
        roles=["yard_admin"],
        request_id="req-office",
    )


def _agent(db, yard_id: str) -> YardAgent:
    agent = YardAgent(
        yard_id=yard_id,
        agent_code="office_weekly_assistant",
        name="办公周报助手",
        description="负责周报与文档排版",
        emoji="📝",
        persona_prompt="你是周报撰写专家，先结论后细节。",
        task_prompt_template="{{question}}",
        allowed_tools=["knowledge.search"],
        bound_skills=["skill:bound-office-skill"],
        knowledge_scopes=["yard"],
        status="published",
        visibility="public",
        version="1.0.0",
        created_from="manual",
    )
    db.add(agent)
    db.flush()
    return agent


def test_bound_agent_document_intent_generates_with_persona(db_session, monkeypatch) -> None:
    yard = create_yard(db_session, name="锁定文档单测小院")
    db_session.flush()
    agent = _agent(db_session, yard.id)
    session = service.create_session(
        db_session,
        yard_id=yard.id,
        created_by="u-office",
        target="agent",
        target_code=agent.agent_code,
    )
    captured: dict = {}

    async def fake_generate(
        db,
        *,
        ctx,
        session_id,
        request_text,
        context="",
        agent_name="",
        agent_persona="",
        agent_bound_skills=None,
    ):
        captured.update(
            {
                "session_id": session_id,
                "request_text": request_text,
                "agent_name": agent_name,
                "agent_persona": agent_persona,
                "bound": list(agent_bound_skills or []),
            }
        )
        return (
            {"type": "document", "filename": "本周周报.docx", "format": "docx"},
            "已生成 Word 文档《本周周报》。",
            {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        )

    monkeypatch.setattr(document_service, "generate_document_from_chat", fake_generate)

    result = asyncio.run(
        service._run_bound_turn(
            db_session,
            ctx=_ctx(yard.id),
            session_id=session.id,
            content="帮我生成一份本周周报",
            attachments=[],
            binding=("agent", agent.agent_code),
            on_step=None,
        )
    )

    assert result["payload"]["filename"] == "本周周报.docx"
    assert result["routed_by"] == "bound"
    assert result["choice"]["name"] == "办公周报助手"
    assert captured["agent_name"] == "办公周报助手"
    assert captured["agent_persona"].startswith("你是周报撰写专家")
    assert captured["bound"] == ["skill:bound-office-skill"]
