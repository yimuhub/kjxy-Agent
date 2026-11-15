"""对话模式服务层：会话 CRUD + 消息持久化 + 复用 supervisor 执行 + 消息/会话级 Token 统计。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.progress import (
    PHASE_GENERATING,
    PHASE_READING,
    PHASE_RETRIEVING,
    PHASE_ROUTING,
    STEP_DONE,
    STEP_FAILED,
    STEP_RUNNING,
    StepCallback,
    StepRecorder,
)
from app.modules.agents import declarative, supervisor
from app.modules.agents.models import YardAgent
from app.modules.audit.service import log_audit
from app.modules.chat import attachments as attachments_service
from app.modules.chat import capability as capability_service
from app.modules.chat import intent as intent_service
from app.modules.chat import media as media_service_chat
from app.modules.chat.models import (
    APPROVAL_ASK,
    APPROVAL_ESCALATION_ROLES,
    APPROVAL_FULL,
    MSG_FAILED,
    MSG_OK,
    ROLE_ASSISTANT,
    ROLE_USER,
    VALID_APPROVAL_MODES,
    ChatAttachment,
    ChatMessage,
    ChatParticipant,
    ChatSession,
)
from app.modules.knowledge.chunking import estimate_token_count

log = get_logger(__name__)

TITLE_CAP = 60
CONTENT_CAP = 8000
#: 指挥官重规划的会话上下文：最近几条消息 + 总字符上限，避免把整段历史塞进规划提示
COMMANDER_HISTORY_LIMIT = 6
COMMANDER_HISTORY_CHARS = 2500
# 助手侧单独放宽：一份完整方案（八章 + 分步细则）常超 8000 字，按输入上限切它会静默丢章节
ASSISTANT_CONTENT_CAP = 20000
ROUTED_LOCAL = "local"

#: 指挥官事件回调：(event, payload)，event ∈ commander-plan|subtask-status|commander-summary
CommanderEventCallback = Callable[[str, dict[str, Any]], None]


from app.core.timezone import utc_now as _utcnow


from app.modules.chat.session_ops import (
    ASSISTANT_CONTENT_CAP,
    BATCH_ACTIONS,
    CONTENT_CAP,
    TITLE_CAP,
    _CITATION_KEYS,
    _CITATION_LIMIT,
    _attachment_ref,
    _attachment_ref_id,
    _bulk_message_stats,
    _chunks,
    _commander_enabled,
    _commander_settings,
    _default_execution_mode,
    _normalize_citations,
    _persist_failure,
    _remap_attachment_ref,
    _session_token_summary,
    apply_execution_mode,
    batch_sessions,
    citations_from_result,
    create_session,
    delete_session,
    derive_title,
    fork_session,
    get_session,
    list_messages,
    list_sessions,
    mention_candidates,
    message_out,
    next_seq,
    persist_assistant_message,
    persist_user_message,
    rename_session,
    resolve_attachments,
    session_out,
    sessions_out_bulk,
    update_session_flags,
)


# ---------------------------------------------------------------------------
# 一次对话的执行（问答链路 / 能力链路）
# ---------------------------------------------------------------------------


def _ensure_generate_permission(db: Session, ctx: YardContext) -> None:
    """对话里的技能/插件生成与 HTTP /market/agent/* 同口径，需要 skills:generate。

    默认角色全员都有该权限；自定义角色可以裁掉——裁掉后对话里同样不可用，
    不能因为走聊天入口就绕过权限矩阵。
    """
    from app.modules.yards.authz import has_permission

    if not has_permission(
        db,
        yard_id=ctx.yard_id,
        user_id=ctx.user_id,
        jwt_roles=ctx.roles or [],
        permission="skills:generate",
    ):
        raise AppError(
            "FORBIDDEN",
            "缺少 skills:generate 权限，无法生成技能或插件。",
            http_status=403,
        )


#: 会话绑定的最近对话注入上限（条数 / 字符数）
CHAT_HISTORY_LIMIT = 8
CHAT_HISTORY_MAX_CHARS = 3000


def session_binding(session: ChatSession | None) -> tuple[str, str] | None:
    """会话级执行对象：``target=agent/team`` 且有 ``target_code`` 才算锁定。

    这是「与某个助手单独对话」的硬边界：锁定后每轮都直达该对象，
    不再进入总控自动路由，也不允许被单条消息的 ``target_code`` 改道。
    """
    if session is None:
        return None
    target = (getattr(session, "target", None) or "auto").strip().lower()
    code = (getattr(session, "target_code", None) or "").strip()
    if target in {"agent", "team"} and code:
        return target, code
    return None


def _recent_conversation(
    db: Session,
    session_id: str,
    current: str,
    *,
    limit: int = CHAT_HISTORY_LIMIT,
    max_chars: int = CHAT_HISTORY_MAX_CHARS,
) -> str:
    """把本会话最近几轮对话压成一段上下文，供锁定助手承接前文。

    当前轮的用户消息在调用前已落库，取数时跳过最新一条，避免原样重复。
    """
    rows = list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.seq.desc())
            .limit(limit + 1)
        )
    )
    if (
        rows
        and rows[0].role == ROLE_USER
        and (rows[0].content or "").strip() == (current or "").strip()
    ):
        rows = rows[1:]
    lines: list[str] = []
    for row in reversed(rows):
        if row.role == ROLE_ASSISTANT and row.status != MSG_OK:
            # 失败/中止的回答不进入上下文，避免把「服务暂时不可用」当成事实
            continue
        text = " ".join((row.content or "").split())
        if not text:
            continue
        prefix = "用户" if row.role == ROLE_USER else "助手"
        lines.append(f"{prefix}：{text[:600]}")
    block = "\n".join(lines)
    if not block:
        return ""
    if len(block) > max_chars:
        block = block[-max_chars:]
    return "【本会话此前的对话（仅用于承接上下文）】\n" + block


async def _run_bound_turn(
    db: Session,
    *,
    ctx: YardContext,
    session_id: str,
    content: str,
    attachments: list[ChatAttachment],
    binding: tuple[str, str],
    on_step: StepCallback | None,
) -> dict[str, Any]:
    """锁定会话的一轮执行：直达绑定对象，只带本会话历史与附件上下文。"""
    kind, target = binding
    label = "班组" if kind == "team" else "助手"
    context_parts: list[str] = []
    if attachments:
        if on_step:
            on_step(PHASE_READING, f"正在阅读 {len(attachments)} 个附件", STEP_RUNNING)
        context_parts.append(attachments_service.build_attachment_context(attachments))
        ready = sum(1 for a in attachments if a.status == "ready")
        if on_step:
            on_step(
                PHASE_READING,
                f"已读取 {len(attachments)} 个附件（{ready} 个可解析）",
                STEP_DONE,
            )
    history = _recent_conversation(db, session_id, content)
    if history:
        context_parts.append(history)
    context = "\n\n".join(part for part in context_parts if part)
    question = f"{content}\n\n{context}" if context else content

    # 锁定助手同样要能用办公技能产出文档：在定向执行前先识别文档意图。
    intent = intent_service.detect(content)
    if intent.kind == intent_service.INTENT_DOC:
        from app.modules.chat import document as document_service

        agent = None
        if kind == "agent":
            agent = declarative.get_published_by_code(
                db, yard_id=ctx.yard_id, agent_code=target, user_id=ctx.user_id
            )
        agent_name = str(getattr(agent, "name", "") or "") if agent else ""
        chosen_name = agent_name or (target if kind == "agent" else "")
        if on_step:
            on_step(PHASE_GENERATING, "正在整理文档内容", STEP_RUNNING)
        card, note, doc_usage = await document_service.generate_document_from_chat(
            db,
            ctx=ctx,
            session_id=session_id,
            request_text=content,
            context=context,
            agent_name=chosen_name,
            agent_persona=str(getattr(agent, "persona_prompt", "") or "") if agent else "",
            agent_bound_skills=list(getattr(agent, "bound_skills", None) or []) if agent else [],
        )
        if on_step:
            on_step(PHASE_GENERATING, f"文档已生成：{card['filename']}", STEP_DONE)
        return {
            "content": note,
            "routed_by": "bound",
            "choice": {
                "kind": kind,
                "id": str(getattr(agent, "id", "") or target),
                "name": chosen_name or "文档生成",
            },
            "reason": f"会话已绑定：{chosen_name or target}（文档生成）",
            "payload": card,
            "citations": [],
            "token_usage": doc_usage,
        }

    if on_step:
        on_step(PHASE_ROUTING, f"本会话已绑定{label}「{target}」，直接执行", STEP_RUNNING)
    data = await supervisor.ask_directed(
        db,
        ctx=ctx,
        question=question,
        target=target,
        prefer_team=kind == "team",
    )
    result = dict(data.get("result") or {})
    choice = dict(data.get("choice") or {})
    chosen_name = str(choice.get("name") or target)
    if on_step:
        on_step(PHASE_ROUTING, f"已由「{chosen_name}」回答（会话绑定）", STEP_DONE)

    answer_text = str(result.get("answer") or "")
    token_usage = result.get("token_usage")
    if not isinstance(token_usage, dict):
        in_tok = estimate_token_count(question)
        out_tok = estimate_token_count(answer_text)
        token_usage = {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
            "estimated": True,
        }
    return {
        "content": answer_text,
        "routed_by": str(data.get("routed_by") or "bound"),
        "choice": choice,
        "reason": str(data.get("reason") or f"会话已绑定：{chosen_name}"),
        "payload": None,
        "citations": citations_from_result(result),
        "token_usage": token_usage,
    }


async def execute_turn(
    db: Session,
    *,
    ctx: YardContext,
    session_id: str,
    content: str,
    attachments: list[ChatAttachment],
    on_step: StepCallback | None = None,
    target_code: str | None = None,
    session: ChatSession | None = None,
) -> dict[str, Any]:
    """执行一轮对话，返回 {content, routed_by, choice, reason, payload, token_usage}。"""
    # 会话级锁定优先于一切自动分流：与某个助手/班组单独对话时，整轮只由它回答。
    binding = session_binding(session)

    # 对话内媒体（v3.73）：规则命中的出图/出片走确定性编排，不依赖模型自觉调工具。
    # 放在锁定执行之前——锁定会话里说「做张海报」也应当出海报，执行者仍优先取锁定对象。
    media_intent = media_service_chat.detect(content)
    if media_intent is not None:
        return await media_service_chat.run_turn(
            db,
            ctx=ctx,
            session_id=session_id,
            content=content,
            intent=media_intent,
            binding=binding,
            target_code=target_code,
            on_step=on_step,
        )

    if binding is not None:
        return await _run_bound_turn(
            db,
            ctx=ctx,
            session_id=session_id,
            content=content,
            attachments=attachments,
            binding=binding,
            on_step=on_step,
        )

    intent = intent_service.detect(content)

    if intent.kind == intent_service.INTENT_CREATE:
        if on_step:
            on_step(PHASE_GENERATING, "正在把你的需求整理成能力草稿", STEP_RUNNING)
        try:
            card, note = await capability_service.generate_capability(
                db, ctx=ctx, request_text=content
            )
        except AppError:
            if on_step:
                on_step(PHASE_GENERATING, "能力生成失败", STEP_FAILED)
            raise
        if on_step:
            on_step(PHASE_GENERATING, "能力草稿已生成", STEP_DONE)
        in_tok = estimate_token_count(content)
        out_tok = estimate_token_count(note) + estimate_token_count(str(card))
        return {
            "content": note,
            "routed_by": ROUTED_LOCAL,
            "choice": {"kind": "agent", "id": card.get("agent_id") or "", "name": card["name"]},
            "reason": intent.reason,
            "payload": card,
            "token_usage": {
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "total_tokens": in_tok + out_tok,
                "model": "capability_builder",
            },
        }

    if intent.kind == intent_service.INTENT_INSTALL:
        from app.modules.market.service import install_from_chat

        if on_step:
            on_step(PHASE_GENERATING, "正在从能力市场匹配并装载", STEP_RUNNING)
        install_result = install_from_chat(db, ctx=ctx, request_text=content)
        if on_step:
            on_step(PHASE_GENERATING, install_result["summary"], STEP_DONE)
        in_tok = estimate_token_count(content)
        out_tok = estimate_token_count(install_result["summary"])
        return {
            "content": install_result["summary"],
            "routed_by": ROUTED_LOCAL,
            "choice": {"kind": "agent", "id": "", "name": install_result["name"]},
            "reason": intent.reason,
            "payload": install_result["card"],
            "token_usage": {
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "total_tokens": in_tok + out_tok,
                "model": "market_installer",
                "estimated": True,
            },
        }

    # ---- 技能链路：一句话要技能（先检索匹配，无匹配再生成私有草稿）----
    if intent.kind == intent_service.INTENT_SKILL:
        # 与 HTTP /market/agent/* 同口径：技能检索/生成需要 skills:generate
        # （默认全员都有；自定义角色裁掉后对话里同样不可用）
        _ensure_generate_permission(db, ctx)
        from app.modules.market.skill_agent import handle_skill_request

        if on_step:
            on_step(PHASE_RETRIEVING, "正在检索已有技能", STEP_RUNNING)
        try:
            skill_card: dict[str, Any] = await handle_skill_request(
                db, ctx=ctx, utterance=content, on_step=on_step
            )
        except AppError:
            # 与能力链路同口径：错误继续上抛（run_and_persist 负责落失败行），
            # 这里只把最后一步标成 failed，让过程可见。
            if on_step:
                on_step(PHASE_GENERATING, "技能检索/生成失败，请稍后重试", STEP_FAILED)
            raise

        state = str(skill_card.get("state") or "")
        raw_skill = skill_card.get("skill")
        skill: dict[str, Any] = raw_skill if isinstance(raw_skill, dict) else {}
        skill_name = str(skill.get("name") or "")
        note = str(skill_card.get("note") or "").strip()
        if state == "failed":
            fail_reason = note or "未生成可用技能草稿"
            if on_step:
                on_step(PHASE_GENERATING, f"技能生成失败：{fail_reason}", STEP_FAILED)
            summary = note or "技能生成失败，请稍后重试或换个说法再试。"
        elif state == "created":
            if on_step:
                on_step(PHASE_GENERATING, "未找到匹配，正在学习并生成技能", STEP_DONE)
                on_step(PHASE_GENERATING, "技能草稿已生成", STEP_DONE)
            summary = note or "已生成技能草稿，可在技能库中查看并绑定到助手。"
        else:
            if on_step:
                matched = len(skill_card.get("matches") or [])
                label = f"「{skill_name}」" if skill_name else ""
                on_step(PHASE_GENERATING, f"已找到 {matched} 个匹配技能{label}", STEP_DONE)
            summary = note or (
                f"已找到匹配技能「{skill_name}」，可在卡片中安装。"
                if skill_name
                else "已找到可用的匹配技能，请在卡片中确认。"
            )

        usage = skill_card.get("token_usage")
        if isinstance(usage, dict):
            skill_token_usage = usage
        else:
            in_tok = estimate_token_count(content)
            out_tok = estimate_token_count(summary) + estimate_token_count(str(skill_card))
            skill_token_usage = {
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "total_tokens": in_tok + out_tok,
                "model": "skill_generator",
                "estimated": True,
            }
        return {
            "content": summary,
            "routed_by": ROUTED_LOCAL,
            "choice": {
                "kind": "skill",
                "id": str(skill.get("id") or ""),
                "name": skill_name or "技能生成",
            },
            "reason": intent.reason,
            "payload": skill_card,
            "token_usage": skill_token_usage,
        }

    # ---- 插件链路：一句话要插件（v3.3：生成 workflow manifest，一律待审）----
    if intent.kind == intent_service.INTENT_PLUGIN:
        _ensure_generate_permission(db, ctx)
        from app.modules.market import plugin_agent

        if on_step:
            on_step(PHASE_GENERATING, "正在设计工作流插件", STEP_RUNNING)
        try:
            plugin_card: dict[str, Any] = await plugin_agent.handle_plugin_request(
                db, ctx=ctx, utterance=content, on_step=on_step
            )
        except AppError:
            # 与能力/技能链路同口径：错误继续上抛（run_and_persist 负责落失败行），
            # 这里只把最后一步标成 failed，让过程可见。
            if on_step:
                on_step(PHASE_GENERATING, "插件生成失败，请稍后重试", STEP_FAILED)
            raise

        state = str(plugin_card.get("state") or "")
        raw_plugin = plugin_card.get("plugin")
        plugin: dict[str, Any] = raw_plugin if isinstance(raw_plugin, dict) else {}
        plugin_name = str(plugin.get("name") or "")
        note = str(plugin_card.get("note") or "").strip()
        if state == "failed":
            fail_reason = note or "未生成可用插件草稿"
            if on_step:
                on_step(PHASE_GENERATING, f"插件生成失败：{fail_reason}", STEP_FAILED)
            summary = note or "插件生成失败，请稍后重试或换个说法再试。"
        else:
            if on_step:
                on_step(PHASE_GENERATING, "插件草稿已生成，等待审核", STEP_DONE)
            summary = note or (
                f"已生成工作流插件「{plugin_name}」，审核通过后即可运行。"
                if plugin_name
                else "已生成工作流插件草稿，审核通过后即可运行。"
            )

        usage = plugin_card.get("token_usage")
        if isinstance(usage, dict):
            plugin_token_usage = usage
        else:
            in_tok = estimate_token_count(content)
            out_tok = estimate_token_count(summary) + estimate_token_count(str(plugin_card))
            plugin_token_usage = {
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "total_tokens": in_tok + out_tok,
                "model": "plugin_generator",
                "estimated": True,
            }
        return {
            "content": summary,
            "routed_by": ROUTED_LOCAL,
            "choice": {
                "kind": "plugin",
                "id": str(plugin.get("plugin_id") or ""),
                "name": plugin_name or "插件生成",
            },
            "reason": intent.reason,
            "payload": plugin_card,
            "token_usage": plugin_token_usage,
        }

    # ---- 文档链路：产出 Word/Excel/PPT/PDF/Markdown 并落为会话附件 ----
    if intent.kind == intent_service.INTENT_DOC:
        from app.modules.chat import document as document_service

        doc_context = ""
        if attachments:
            if on_step:
                on_step(PHASE_READING, f"正在阅读 {len(attachments)} 个附件", STEP_RUNNING)
            doc_context = attachments_service.build_attachment_context(attachments)
            if on_step:
                on_step(PHASE_READING, f"已读取 {len(attachments)} 个附件", STEP_DONE)
        if on_step:
            on_step(PHASE_GENERATING, "正在整理文档内容", STEP_RUNNING)
        agent = (
            declarative.get_published_by_code(
                db, yard_id=ctx.yard_id, agent_code=target_code, user_id=ctx.user_id
            )
            if target_code
            else None
        )
        card, note, doc_usage = await document_service.generate_document_from_chat(
            db,
            ctx=ctx,
            session_id=session_id,
            request_text=content,
            context=doc_context,
            agent_name=str(getattr(agent, "name", "") or "") if agent else "",
            agent_persona=str(getattr(agent, "persona_prompt", "") or "") if agent else "",
            agent_bound_skills=(list(getattr(agent, "bound_skills", None) or []) if agent else []),
        )
        if on_step:
            on_step(PHASE_GENERATING, f"文档已生成：{card['filename']}", STEP_DONE)
        return {
            "content": note,
            "routed_by": ROUTED_LOCAL,
            "choice": {
                "kind": "agent",
                "id": str(getattr(agent, "id", "") or ""),
                "name": str(getattr(agent, "name", "") or "") or "文档生成",
            },
            "reason": intent.reason,
            "payload": card,
            "token_usage": doc_usage,
        }

    # ---- 问答链路：附件只进上下文，不参与路由打分 ----
    context = ""
    if attachments:
        if on_step:
            on_step(PHASE_READING, f"正在阅读 {len(attachments)} 个附件", STEP_RUNNING)
        context = attachments_service.build_attachment_context(attachments)
        ready = sum(1 for a in attachments if a.status == "ready")
        if on_step:
            on_step(
                PHASE_READING,
                f"已读取 {len(attachments)} 个附件（{ready} 个可解析）",
                STEP_DONE,
            )

    # ---- 方案链路：多源取证合成完整方案（v3.14）----
    if intent.kind == intent_service.INTENT_PLAN and not target_code:
        from app.modules.chat import plan as plan_service

        if on_step:
            on_step(PHASE_ROUTING, "按方案链路合成完整方案", STEP_RUNNING)
        outcome = await plan_service.build_plan(
            db,
            ctx=ctx,
            session_id=session_id,
            question=content,
            context=context,
            on_step=on_step,
        )
        return {**outcome, "citations": _normalize_citations(outcome.get("citations"))}

    if target_code:
        if on_step:
            on_step(PHASE_ROUTING, f"按 @ 指定定向执行「{target_code}」", STEP_RUNNING)
        question = f"{content}\n\n{context}" if context else content
        data = await supervisor.ask_directed(db, ctx=ctx, question=question, target=target_code)
        chosen_name = str((data.get("choice") or {}).get("name") or target_code)
        if on_step:
            on_step(PHASE_ROUTING, f"已定向「{chosen_name}」（定向执行）", STEP_DONE)
    else:
        data = await supervisor.ask(db, ctx=ctx, question=content, on_step=on_step, context=context)
    result: dict[str, Any] = dict(data.get("result") or {})
    choice: dict[str, Any] = dict(data.get("choice") or {})
    if result.get("run_id"):
        choice["run_id"] = result["run_id"]

    answer_text = str(result.get("answer") or "")
    token_usage = result.get("token_usage")
    if not isinstance(token_usage, dict):
        in_tok = estimate_token_count(f"{content}\n{context}")
        out_tok = estimate_token_count(answer_text)
        token_usage = {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
            "estimated": True,
        }

    return {
        "content": answer_text,
        "routed_by": str(data.get("routed_by") or ""),
        "choice": choice,
        "reason": str(data.get("reason") or ""),
        "payload": None,
        "citations": citations_from_result(result),
        "token_usage": token_usage,
    }


def scoped_ctx(ctx: YardContext, session: ChatSession) -> YardContext:
    """把「本会话的授权模式 + 会话 id」带进本轮执行上下文。

    工具风险闸（``tools/registry``）据此在 ask/auto/full 之间分流；不带会话 id 时
    一律退回 ask——任务、机器人、脚本等非对话入口的行为逐字不变。
    """
    return replace(
        ctx,
        approval_mode=session.approval_mode or APPROVAL_ASK,
        session_id=session.id,
        approval_requests=[],
    )


def approval_card(requests: list[dict[str, Any]]) -> dict[str, Any]:
    """被风险闸拦下的调用 → 对话里的「去批准」卡片（前端只渲染，不自行拼 id）。"""
    return {
        "type": "approval",
        "title": "高风险操作待批准",
        "hint": "本轮有工具需人工批准后才会执行；批准后可重新提问。",
        "items": [
            {
                "tool_code": str(r.get("tool_code") or ""),
                "approval_id": str(r.get("approval_id") or ""),
            }
            for r in requests
            if r.get("approval_id")
        ],
    }


def _bridge_commander_events(
    cb: CommanderEventCallback | None,
    run_id: str,
) -> Callable[[str, dict[str, Any]], None] | None:
    """把 commander 服务的事件名（plan/subtask/summary）映射成 SSE 帧名。

    服务层的 payload 是「运行/子任务对象」本身（plan/subtask 不带 run_id、
    子任务主键叫 id），这里补齐帧契约字段，前端才能按 run_id 归档。
    """
    if cb is None:
        return None
    mapping = {
        "plan": "commander-plan",
        "subtask": "subtask-status",
        "summary": "commander-summary",
    }

    def _bridge(kind: str, payload: dict[str, Any]) -> None:
        event = mapping.get(kind)
        if not event:
            return
        data = {"run_id": run_id, **payload}
        if kind == "subtask":
            data["subtask_id"] = str(payload.get("id") or payload.get("subtask_id") or "")
        cb(event, data)

    return _bridge


def _commander_token_usage(tokens: int) -> dict[str, Any]:
    total = max(0, int(tokens or 0))
    return {
        "input_tokens": 0,
        "output_tokens": total,
        "total_tokens": total,
        "model": "commander",
        "estimated": False,
    }


def _commander_history_context(db: Session, session: ChatSession, current: str) -> str:
    """把最近几条对话压成规划上下文。

    「第 3 页排版不好」这类反馈必须能对上上一版成品，否则指挥官会当成新任务重做。
    只取最近 ``COMMANDER_HISTORY_LIMIT`` 条（排除本轮刚落的用户消息），
    并把上一条答复里的产物标题一并带上，总长限制在 ``COMMANDER_HISTORY_CHARS``。
    """
    rows = list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.session_id == session.id)
            .order_by(ChatMessage.seq.desc())
            .limit(COMMANDER_HISTORY_LIMIT + 1)
        )
    )
    rows.reverse()
    if rows and rows[-1].role == ROLE_USER and (rows[-1].content or "").strip() == current.strip():
        rows.pop()
    lines: list[str] = []
    for msg in rows[-COMMANDER_HISTORY_LIMIT:]:
        text = (msg.content or "").strip()
        if not text:
            continue
        if msg.role == ROLE_USER:
            label = "用户"
        elif msg.routed_by == "commander":
            label = "指挥官"
        else:
            label = "助手"
        item = f"[{label}] {text[:600]}"
        payload = msg.payload_json if isinstance(msg.payload_json, dict) else {}
        artifacts = payload.get("artifacts")
        if isinstance(artifacts, list):
            titles = [
                str(art.get("title") or art.get("name") or "").strip()
                for art in artifacts
                if isinstance(art, dict)
            ]
            titles = [title for title in titles if title][:5]
            if titles:
                item += f"\n（上一版产物：{'、'.join(titles)}）"
        lines.append(item)
    if not lines:
        return ""
    return (
        "最近对话（用于理解用户对上一版成品的修改意见；只作上下文，不要重复交付）：\n"
        + "\n".join(lines)
    )[:COMMANDER_HISTORY_CHARS]


async def _run_commander_turn(
    db: Session,
    *,
    ctx: YardContext,
    session: ChatSession,
    content: str,
    attachments: list[ChatAttachment],
    steps: StepRecorder,
    on_step: StepCallback | None,
    on_commander_event: CommanderEventCallback | None,
    clarify_run_id: str | None = None,
) -> dict[str, Any]:
    """指挥官一轮：拆解计划 →（预览或）并行执行 → 汇总落库。

    commander 模块全部懒导入：它是可选增强，模块自身出问题不能连普通单聊一起挂。
    """
    from app.modules.commander import service as commander_service
    from app.modules.commander import settings as commander_settings
    from app.modules.commander.models import CommanderRun

    cfg = commander_settings.settings_for(db, session.yard_id)
    display_name = str(cfg.get("display_name") or "指挥官")
    context = attachments_service.build_attachment_context(attachments) if attachments else ""
    history = _commander_history_context(db, session, content)
    if history:
        context = f"{context}\n\n{history}".strip() if context else history
    emit = on_step or steps.emit

    # 澄清续跑：把原目标与用户补充合并成一条完整目标，并跳过二次追问
    base_goal = ""
    if clarify_run_id:
        prev = db.get(CommanderRun, clarify_run_id)
        if prev is not None and prev.yard_id == ctx.yard_id and prev.user_id == ctx.user_id:
            base_goal = str(prev.goal or "").strip()
    if base_goal and base_goal not in content:
        content = f"{base_goal}\n\n用户补充（以此为准）：{content}"

    emit(PHASE_ROUTING, f"{display_name}正在拆解任务", STEP_RUNNING)
    run = await commander_service.create_plan_run(
        db,
        ctx=ctx,
        session_id=session.id,
        user_content=content,
        context=context,
        allow_clarify=not clarify_run_id,
    )
    plan_json = getattr(run, "plan_json", None) or {}
    # 兼容两种存量形状：结构化表单项 / 早期纯字符串问题
    clarify: list[dict[str, Any]] = []
    for index, item in enumerate(plan_json.get("clarify") or [], start=1):
        if isinstance(item, dict) and str(item.get("label") or "").strip():
            clarify.append(item)
        elif isinstance(item, str) and item.strip():
            clarify.append(
                {
                    "key": f"q{index}",
                    "label": item.strip()[:200],
                    "type": "text",
                    "options": [],
                    "allow_custom": True,
                    "required": False,
                }
            )
    # 完全控制 = 不问：连澄清也跳过，按最合理假设直接开跑；只有轮尽才回报缺口
    if (
        clarify
        and cfg.get("clarify_enabled", False)
        and (session.approval_mode or APPROVAL_ASK) != APPROVAL_FULL
    ):
        # 目标缺必要信息：先弹表单问 1–2 个可点选的问题，答完自动续跑
        labels = [str(item.get("label") or "").strip() for item in clarify]
        text = f"为了把任务拆准，{display_name}需要先确认 {len(clarify)} 项：\n" + "\n".join(
            f"{i}. {label}" for i, label in enumerate(labels, start=1)
        )
        emit(PHASE_ROUTING, f"信息不足，先确认 {len(clarify)} 项", STEP_DONE)
        choice = {"kind": "commander", "id": run.id, "name": display_name}
        plan_tokens = _commander_token_usage(run.tokens_total)
        payload = {
            "type": "commander_clarify",
            "run_id": run.id,
            "goal": str(getattr(run, "goal", "") or ""),
            "display_name": display_name,
            "questions": clarify,
            "feasibility": plan_json.get("feasibility") or {},
        }
        assistant_msg = persist_assistant_message(
            db,
            ctx=ctx,
            session=session,
            content=text,
            routed_by="commander",
            choice=choice,
            reason="指挥官澄清",
            payload=payload,
            steps=steps.steps or None,
            token_usage=plan_tokens,
        )
        return {
            "assistant_message": assistant_msg,
            "content": text,
            "routed_by": "commander",
            "choice": choice,
            "reason": "指挥官澄清",
            "payload": payload,
            "steps": steps.steps,
            "citations": [],
            "token_usage": plan_tokens,
        }

    _, planned_subtasks = commander_service.get_run(db, ctx.yard_id, run.id)
    plan = commander_service.plan_message_payload(run, planned_subtasks)
    if on_commander_event is not None:
        on_commander_event("commander-plan", plan)
    count = len(plan.get("subtasks") or [])
    emit(PHASE_ROUTING, f"计划已生成：{count} 个子任务", STEP_DONE)

    choice = {"kind": "commander", "id": run.id, "name": display_name}
    plan_tokens = _commander_token_usage(run.tokens_total)

    if cfg.get("preview_plan") and (session.approval_mode or APPROVAL_ASK) != APPROVAL_FULL:
        notice = f"已生成计划：共 {count} 个子任务，请确认后开始执行。"
        payload = {**plan, "run_id": run.id, "status": run.status}
        assistant_msg = persist_assistant_message(
            db,
            ctx=ctx,
            session=session,
            content=notice,
            routed_by="commander",
            choice=choice,
            reason="指挥官计划待确认",
            payload=payload,
            steps=steps.steps or None,
            token_usage=plan_tokens,
        )
        return {
            "assistant_message": assistant_msg,
            "content": notice,
            "routed_by": "commander",
            "choice": choice,
            "reason": "指挥官计划待确认",
            "payload": payload,
            "steps": steps.steps,
            "citations": [],
            "token_usage": plan_tokens,
        }

    # create_plan_run 内部已 commit；执行器自开短连接，本会话不能持有写事务
    emit(PHASE_GENERATING, f"{display_name}正在调度子任务并行执行", STEP_RUNNING)
    summary = await commander_service.execute_run(
        run.id, on_event=_bridge_commander_events(on_commander_event, run.id)
    )
    emit(PHASE_GENERATING, "执行完成，正在汇总", STEP_DONE)

    # 执行器在另一条连接上提交，本会话必须清缓存再读，否则拿到的是计划阶段的旧状态
    db.expire_all()
    latest_run, latest_subtasks = commander_service.get_run(db, ctx.yard_id, run.id)
    payload = {
        "type": "commander_summary",
        **commander_service.run_out(latest_run, latest_subtasks),
        "artifacts": summary.get("artifacts") or [],
        "failed": summary.get("failed") or [],
    }
    text = str(summary.get("answer") or f"{display_name}已完成本轮编排。")
    token_usage = _commander_token_usage(summary.get("tokens_total"))
    assistant_msg = persist_assistant_message(
        db,
        ctx=ctx,
        session=session,
        content=text,
        routed_by="commander",
        choice=choice,
        reason="指挥官编排",
        payload=payload,
        steps=steps.steps or None,
        token_usage=token_usage,
    )
    return {
        "assistant_message": assistant_msg,
        "content": text,
        "routed_by": "commander",
        "choice": choice,
        "reason": "指挥官编排",
        "payload": payload,
        "steps": steps.steps,
        "citations": [],
        "token_usage": token_usage,
    }


async def run_and_persist(
    db: Session,
    *,
    ctx: YardContext,
    session: ChatSession,
    content: str,
    attachments: list[ChatAttachment],
    on_step: StepCallback | None = None,
    recorder: StepRecorder | None = None,
    target_code: str | None = None,
    execution_mode: str | None = None,
    clarify_run_id: str | None = None,
    on_commander_event: CommanderEventCallback | None = None,
) -> dict[str, Any]:
    ctx = scoped_ctx(ctx, session)
    steps = recorder or StepRecorder()

    def _cb(phase: str, detail: str, status: str) -> None:
        steps.emit(phase, detail, status)
        if on_step is not None:
            on_step(phase, detail, status)

    mode = apply_execution_mode(db, session, execution_mode)

    try:
        if mode == "commander" and target_code is None:
            return await _run_commander_turn(
                db,
                ctx=ctx,
                session=session,
                content=content,
                attachments=attachments,
                steps=steps,
                on_step=_cb,
                on_commander_event=on_commander_event,
                clarify_run_id=clarify_run_id,
            )
        outcome = await execute_turn(
            db,
            ctx=ctx,
            session_id=session.id,
            content=content,
            attachments=attachments,
            on_step=_cb,
            target_code=target_code,
            session=session,
        )
    except AppError as e:
        steps.fail(PHASE_GENERATING, e.message)
        blocked = (e.details or {}).get("approval_id") if e.code == "TOOL_RISK_BLOCKED" else None
        _persist_failure(
            db,
            session,
            ctx,
            code=e.code,
            message=e.message,
            steps=steps.steps,
            payload=approval_card(ctx.approval_requests)
            if blocked or ctx.approval_requests
            else None,
        )
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("chat send unexpected error: %s", e)
        steps.fail(PHASE_GENERATING, "服务暂时不可用")
        _persist_failure(
            db, session, ctx, code="INTERNAL_ERROR", message="服务暂时不可用。", steps=steps.steps
        )
        raise

    steps.finish()
    payload = outcome["payload"]
    if ctx.approval_requests:
        # 模型可能已把「需要审批」写进回答，但卡片给出可点的审批单号，
        # 用户不必自己去找审批页；已有其他卡片时不覆盖，只补进步骤痕迹。
        if payload is None:
            payload = approval_card(ctx.approval_requests)
        else:
            # 本轮同时产出了别的卡片（方案/文档/技能…）：不能为了塞审批卡把它顶掉，
            # 但被拦下的调用也必须可点——把同一份 items 附在原卡片上。
            payload = {
                **payload,
                "approvals": approval_card(ctx.approval_requests)["items"],
            }
            steps.emit(
                PHASE_GENERATING,
                f"本轮有 {len(ctx.approval_requests)} 个高风险调用待批准",
                STEP_DONE,
            )
    assistant_msg = persist_assistant_message(
        db,
        ctx=ctx,
        session=session,
        content=outcome["content"],
        routed_by=outcome["routed_by"],
        choice=outcome["choice"],
        reason=outcome["reason"],
        payload=payload,
        steps=steps.steps or None,
        citations=outcome.get("citations"),
        token_usage=outcome.get("token_usage"),
    )

    # 媒体卡片：把资产绑定到本条消息，视频完成时后台才能原地回填同一张卡片
    media_ids = media_service_chat.pending_asset_ids(payload)
    if media_ids:
        from app.modules.media import service as media_assets
        from app.modules.media.models import MediaAsset

        rows = [db.get(MediaAsset, asset_id) for asset_id in media_ids]
        media_assets.mark_message(db, [row for row in rows if row is not None], assistant_msg.id)
        db.commit()

    return {
        "assistant_message": assistant_msg,
        "content": outcome["content"],
        "routed_by": outcome["routed_by"],
        "choice": outcome["choice"],
        "reason": outcome["reason"],
        "payload": payload,
        "steps": steps.steps,
        "citations": outcome.get("citations") or [],
        "token_usage": outcome.get("token_usage"),
    }


# ---------------------------------------------------------------------------
# 对外主入口
# ---------------------------------------------------------------------------


async def send_message(
    db: Session,
    *,
    ctx: YardContext,
    session_id: str,
    content: str,
    attachment_ids: list[str] | None = None,
    on_step: StepCallback | None = None,
    target_code: str | None = None,
    execution_mode: str | None = None,
    clarify_run_id: str | None = None,
    on_commander_event: CommanderEventCallback | None = None,
) -> dict[str, Any]:
    content = (content or "").strip()[:CONTENT_CAP]
    if not content:
        raise AppError("INVALID_REQUEST", "消息内容不能为空。", http_status=400)
    session = get_session(db, yard_id=ctx.yard_id, created_by=ctx.user_id, session_id=session_id)
    attachments = resolve_attachments(
        db, ctx=ctx, session_id=session.id, attachment_ids=attachment_ids
    )
    user_msg = persist_user_message(
        db, ctx=ctx, session=session, content=content, attachments=attachments
    )
    db.commit()

    outcome = await run_and_persist(
        db,
        ctx=ctx,
        session=session,
        content=content,
        attachments=attachments,
        on_step=on_step,
        target_code=target_code,
        execution_mode=execution_mode,
        clarify_run_id=clarify_run_id,
        on_commander_event=on_commander_event,
    )
    assistant_msg = outcome["assistant_message"]
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="chat_message_send",
        resource_type="chat_session",
        resource_id=session.id,
        result="success",
        request_id=ctx.request_id,
        metadata={
            "user_seq": user_msg.seq,
            "assistant_seq": assistant_msg.seq,
            "routed_by": assistant_msg.routed_by,
            "kind": (outcome["choice"] or {}).get("kind"),
            "name": (outcome["choice"] or {}).get("name"),
            "attachments": len(attachments),
            "tokens": (outcome.get("token_usage") or {}).get("total_tokens"),
        },
    )
    db.commit()
    return {
        "session": session_out(db, session),
        "user_message": message_out(user_msg),
        "assistant_message": message_out(assistant_msg),
    }


__all__ = [
    "BATCH_ACTIONS",
    "CONTENT_CAP",
    "ROUTED_LOCAL",
    "approval_card",
    "batch_sessions",
    "create_session",
    "delete_session",
    "derive_title",
    "execute_turn",
    "fork_session",
    "get_session",
    "list_messages",
    "list_sessions",
    "mention_candidates",
    "message_out",
    "next_seq",
    "persist_assistant_message",
    "persist_user_message",
    "rename_session",
    "resolve_attachments",
    "run_and_persist",
    "scoped_ctx",
    "send_message",
    "session_out",
    "sessions_out_bulk",
    "update_session_flags",
]
