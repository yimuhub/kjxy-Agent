"""对话内方案合成（v3.14）：多源取证 + 完整性契约，产出可直接执行的完整方案。

单独开这条链路的原因：「写一份…方案」原先被文档链路吞掉，正文进了文件、对话里只剩一句
「已生成」，而那条链路既不查知识库也不联网。方案要求当场看得到、每条结论有出处、
缺证据必须写明待确认，而不是被打包成文件后不知所云。
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.progress import (
    PHASE_GENERATING,
    PHASE_RETRIEVING,
    PHASE_TOOL_CALL,
    STEP_DONE,
    STEP_RUNNING,
    StepCallback,
)
from app.modules.agents.declarative import skills_prompt_for
from app.modules.agents.teams.research import collect_research, research_context
from app.modules.chat.models import MSG_FAILED, ROLE_ASSISTANT, ChatMessage
from app.modules.llm.schemas import ChatMessage as LLMMessage
from app.modules.llm.schemas import LLMMetadata, LLMRequest
from app.modules.llm.service import complete_with_logging
from app.modules.market.skill_agent import select_relevant_skill_codes

log = get_logger(__name__)

ROUTED_PLAN = "plan"

#: 知识库取证条数：方案要覆盖水肥/病虫/成本多个侧面，比问答默认 5 条略宽
KB_TOP_K = 8
#: 会话历史窗口：只带最近几条，让「把上面那份写完整」这类追问能接得住
HISTORY_MESSAGES = 6
HISTORY_CHAR_CAP = 1200
#: 单章正文少于该字数视为没写实（防止模型只写标题交差）
SECTION_MIN_CHARS = 24

#: 完整性契约：章节标题 + 可接受的同族写法（模型常把「实施步骤」写成「关键措施」）
SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("目标与适用条件", ("目标", "适用")),
    ("依据与来源", ("依据", "来源")),
    ("实施步骤", ("步骤", "措施")),
    ("时间与用量安排", ("时间", "排期", "用量")),
    ("风险与安全间隔", ("风险", "安全间隔")),
    ("成本与投入", ("成本", "投入")),
    ("验收指标", ("验收", "指标")),
    ("待确认事项", ("待确认", "待补充", "缺口")),
)

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*(.+?)\s*$|^\s*([^\s#].{0,40}?)\s*[：:]\s*$", re.M)


def _system_prompt() -> str:
    lines = "\n".join(f"## {name}" for name, _ in SECTIONS)
    return (
        "你是科技小院的方案合成器。基于给定证据写一份可直接执行的完整方案，使用 Markdown，"
        "并依次输出以下二级标题章节：\n"
        f"{lines}\n"
        "写作规则：\n"
        "1) 每个章节都要写实：只给标题、写「略」或把几章合并都算未完成；"
        "步骤用有序列表，每条包含「做什么—何时—判定标准」。\n"
        "2) 引用纪律：来自小院知识库的句子标 [K1] 这类编号，来自联网搜索摘要标 [W1] 这类编号；"
        "没有出处的数值与判断必须紧跟「（模型推断，需当地核实）」。\n"
        "3) 证据不足不得编造：把缺口写进「待确认事项」，并在对应章节说明缺什么资料。\n"
        "4) 涉及农药与用药：只可引用证据中已登记的药剂名称与安全间隔期，不得自拟品种、剂量、"
        "次数与混配；未登记的药剂一律写「需专家确认，以产品标签与当地监管为准」。\n"
        "5) 面向小院技术人员与农户，务实具体；篇幅按内容需要展开，不为省事压缩章节。"
    )


def _heading_texts(text: str) -> list[str]:
    return [
        (match.group(1) or match.group(2) or "").strip()
        for match in _HEADING_RE.finditer(text or "")
    ]


def _missing_sections(text: str) -> list[str]:
    """按章节标题（含同族写法）检查成稿是否齐全。"""
    headings = _heading_texts(text)
    missing: list[str] = []
    for name, aliases in SECTIONS:
        hit = any(any(alias in heading for alias in aliases) for heading in headings)
        if not hit:
            missing.append(name)
    return missing


def _split_sections(text: str) -> list[dict[str, str]]:
    """按二级标题切章，供前端分节渲染与导出文档复用。"""
    blocks: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in (text or "").splitlines():
        heading = re.match(r"^\s{0,3}##\s+(.+?)\s*$", line)
        if heading:
            if current is not None:
                blocks.append(current)
            current = {"heading": heading.group(1).strip(), "body": ""}
            continue
        if current is not None:
            current["body"] = f"{current['body']}\n{line}"
    if current is not None:
        blocks.append(current)
    return [
        {**block, "body": block["body"].strip()}
        for block in blocks
        if block["body"].strip() or len(block["heading"]) <= 40
    ]


def _thin_sections(sections: list[dict[str, str]]) -> list[str]:
    """章节在但没写实（正文过短）同样视为不完整。"""
    return [
        block["heading"]
        for block in sections
        if len(re.sub(r"\s", "", block["body"])) < SECTION_MIN_CHARS
    ]


def _history_text(db: Session, *, session_id: str, yard_id: str) -> str:
    """本轮之前的最近几轮对话：让「把上面那份写完整」这类追问接得住上文。"""
    last_seq = db.scalar(
        select(func.max(ChatMessage.seq)).where(ChatMessage.session_id == session_id)
    )
    rows = list(
        db.scalars(
            select(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.yard_id == yard_id,
                ChatMessage.seq < int(last_seq or 0),
            )
            .order_by(ChatMessage.seq.desc())
            .limit(HISTORY_MESSAGES)
        )
    )
    lines: list[str] = []
    for row in reversed(rows):
        if row.role == ROLE_ASSISTANT and row.status == MSG_FAILED:
            continue
        text = re.sub(r"\s+", " ", row.content or "").strip()
        if text:
            lines.append(f"{'助手' if row.role == ROLE_ASSISTANT else '用户'}：{text[:400]}")
    return "\n".join(lines)[-HISTORY_CHAR_CAP:]


def _plan_citations(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """引用列表：与提示词里的 [K#]/[W#] 编号一一对应，前端可点开溯源。"""
    citations: list[dict[str, Any]] = []
    for index, item in enumerate(evidence.get("knowledge") or []):
        if isinstance(item, dict):
            citations.append({**item, "reference": f"K{index + 1}", "kind": "knowledge"})
    for index, item in enumerate(evidence.get("web") or []):
        if isinstance(item, dict):
            citations.append({**item, "reference": f"W{index + 1}", "kind": "web"})
    return citations


def _usage_add(usage: dict[str, Any], resp: Any) -> dict[str, Any]:
    """累计多次模型调用的用量（成稿 + 补章 + 续写都算进这条消息的成本）。"""
    in_tok = int(resp.input_tokens or 0) if resp.input_tokens is not None else 0
    out_tok = int(resp.output_tokens or 0) if resp.output_tokens is not None else 0
    return {
        "input_tokens": int(usage.get("input_tokens") or 0) + in_tok,
        "output_tokens": int(usage.get("output_tokens") or 0) + out_tok,
        "total_tokens": int(usage.get("total_tokens") or 0) + in_tok + out_tok,
        "model": resp.model or usage.get("model") or "",
    }


async def _complete(
    db: Session,
    *,
    ctx: YardContext,
    session_id: str,
    system: str,
    user: str,
    purpose: str,
) -> Any:
    return await complete_with_logging(
        db,
        LLMRequest(
            messages=[
                LLMMessage(role="system", content=system),
                LLMMessage(role="user", content=user),
            ],
            max_tokens=settings.plan_max_tokens,
            timeout_seconds=settings.plan_timeout_seconds,
            temperature=0.3,
            metadata=LLMMetadata(
                yard_id=ctx.yard_id,
                user_id=ctx.user_id,
                session_id=session_id,
                request_id=ctx.request_id,
                purpose=purpose,
                agent_ref="chat_plan",
            ),
        ),
    )


async def build_plan(
    db: Session,
    *,
    ctx: YardContext,
    session_id: str,
    question: str,
    context: str = "",
    on_step: StepCallback | None = None,
) -> dict[str, Any]:
    """多源合成一份完整方案，返回可直接落库的助手消息字段。"""
    if on_step:
        on_step(PHASE_RETRIEVING, "检索小院知识库", STEP_RUNNING)
    evidence = await collect_research(
        db,
        ctx=ctx,
        question=question,
        kb_top_k=KB_TOP_K,
        web="auto",
        web_min_kb_hits=settings.plan_kb_min_hits,
    )
    kb_count = len(evidence.get("knowledge") or [])
    web_count = len(evidence.get("web") or [])
    warnings = list(evidence.get("warnings") or [])
    if on_step:
        on_step(PHASE_RETRIEVING, f"知识库命中 {kb_count} 条资料", STEP_DONE)
        skipped = evidence.get("web_skipped")
        if skipped == "knowledge_enough":
            on_step(PHASE_TOOL_CALL, "知识库证据充足，未联网检索", STEP_DONE)
        elif skipped == "disabled":
            on_step(PHASE_TOOL_CALL, "本次未启用联网检索", STEP_DONE)
        else:
            on_step(PHASE_TOOL_CALL, f"联网补充公开资料 {web_count} 条", STEP_DONE)

    if on_step:
        on_step(PHASE_TOOL_CALL, "匹配小院已发布技能", STEP_RUNNING)
    skills = select_relevant_skill_codes(db, ctx=ctx, text=question, limit=3)
    skills_prompt = skills_prompt_for(
        db,
        ctx.yard_id,
        [s["code"] for s in skills],
        record_source="plan",
        session_id=session_id,
    )
    skill_names = [s["name"] for s in skills]
    if on_step:
        on_step(
            PHASE_TOOL_CALL,
            f"已引用技能：{'、'.join(skill_names)}" if skill_names else "未匹配到相关技能",
            STEP_DONE,
        )

    history = _history_text(db, session_id=session_id, yard_id=ctx.yard_id)
    material = "\n\n".join(
        block
        for block in (
            research_context(evidence, tag="plan_evidence"),
            skills_prompt.strip(),
            f"<conversation_history>\n{history}\n</conversation_history>" if history else "",
            f"<attachments>\n{context}\n</attachments>" if context else "",
        )
        if block
    )
    user_content = (
        f"{material}\n\n请就下面的需求写出完整方案：\n{question}" if material else question
    )

    if on_step:
        on_step(PHASE_GENERATING, "合并知识库、联网资料、技能与模型知识成稿", STEP_RUNNING)
    response = await _complete(
        db,
        ctx=ctx,
        session_id=session_id,
        system=_system_prompt(),
        user=user_content,
        purpose="chat_plan",
    )
    draft = (response.content or "").strip()
    usage = _usage_add({}, response)
    truncated = response.finish_reason == "length"
    if truncated and draft:
        # 被供应商上限截断：先续写一次，而不是把半句话当成完整方案交出去
        tail = draft[-400:]
        cont = await _complete(
            db,
            ctx=ctx,
            session_id=session_id,
            system=(
                "继续写完上一段被截断的方案。从截断处接着输出剩余内容，"
                "不要重复已经写出的文字，保持原有章节与引用纪律。"
            ),
            user=f"已写出内容的结尾如下：\n{tail}\n\n原需求：{question}",
            purpose="chat_plan_continue",
        )
        extra = (cont.content or "").strip()
        if extra:
            draft = f"{draft}\n{extra}"
        usage = _usage_add(usage, cont)
        truncated = cont.finish_reason == "length"

    repairs = 0
    missing = _missing_sections(draft)
    thin = _thin_sections(_split_sections(draft))
    while (missing or thin) and repairs < settings.plan_repair_passes:
        repairs += 1
        gaps = "、".join(missing + thin)
        if on_step:
            on_step(PHASE_GENERATING, f"补齐缺失章节：{gaps}", STEP_RUNNING)
        fix = await _complete(
            db,
            ctx=ctx,
            session_id=session_id,
            system=(
                "上一份方案缺少下列章节或章节写得过浅，只补写这些章节的 Markdown 正文"
                "（每章带 ## 标题，内容与已有章节不重复），遵守引用纪律："
                f"{gaps}"
            ),
            user=f"资料与已有方案如下，请只补写缺失章节：\n{material}\n\n已有方案：\n{draft[:6000]}",
            purpose="chat_plan_repair",
        )
        patch = (fix.content or "").strip()
        if patch:
            draft = f"{draft}\n\n{patch}" if draft else patch
        usage = _usage_add(usage, fix)
        missing = _missing_sections(draft)
        thin = _thin_sections(_split_sections(draft))

    if not draft:
        # 空正文比"缺章节"更严重：落库一条空的"方案"就是假完成，直接按失败处理
        raise AppError(
            "PLAN_EMPTY", "方案合成没有返回内容，请稍后重试或换个说法。", http_status=502
        )

    if missing or thin:
        warnings.append(
            f"仍有章节未写实：{'、'.join(missing + thin)}；请补充资料后重试或转专家复核。"
        )
    if truncated:
        warnings.append("内容在模型输出上限处仍被截断，可调大 PLAN_MAX_TOKENS 或分段追问。")
    if not kb_count and not web_count:
        warnings.append(
            "本次没有小院知识库与联网证据，方案依据模型一般知识成稿，投产前必须由小院专家核实。"
        )
        if on_step:
            on_step(PHASE_GENERATING, "无外部证据，已标注需专家核实", STEP_DONE)

    complete = not missing and not thin and not truncated
    if on_step:
        on_step(
            PHASE_GENERATING,
            "方案已成稿" if complete else "方案有缺口，已如实标注",
            STEP_DONE,
        )

    return {
        "content": draft,
        "routed_by": ROUTED_PLAN,
        "choice": {"kind": "plan", "id": "", "code": "chat_plan", "name": "多源方案合成"},
        "reason": f"知识库 {kb_count} 条 · 联网 {web_count} 条 · 技能 {len(skill_names)} 项",
        "citations": _plan_citations(evidence),
        "token_usage": usage,
        "payload": {
            "type": "plan",
            "title": question[:60],
            "sections": _split_sections(draft),
            "required_sections": [name for name, _ in SECTIONS],
            "missing_sections": missing + thin,
            "complete": complete,
            "truncated": truncated,
            "repair_passes": repairs,
            "needs_expert_review": not complete or not kb_count,
            "evidence": {
                "knowledge": kb_count,
                "web": web_count,
                "skills": skill_names,
                "web_skipped": evidence.get("web_skipped") or "",
            },
            "warnings": warnings,
        },
    }


__all__ = ["build_plan"]
