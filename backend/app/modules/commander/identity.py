"""指挥官身份与技能清单（v3.51）。

诉求：指挥官不只是「派活的调度员」，而是小院里技能点最齐的那个 Agent——联网搜索、
自建助手、文档/PPT 生成都挂在它身上，并且能在模型中心单独给它选大模型。

落地：每个小院落一行 ``yard_agents``（``agent_code="commander"``、已发布），
``bound_skills`` / ``allowed_tools`` 就是它的技能清单，设置页、模型中心与亲自执行
三条链路读同一行。亲自执行时不全量塞提示词（上百项技能会瞬间撑爆上下文），
而是按子任务文本从清单里取最相关的若干项规范注入。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.agents import bindable
from app.modules.agents.models import (
    CREATED_FROM_COMMANDER,
    RISK_LOW,
    STATUS_DELETED,
    STATUS_PUBLISHED,
    VISIBILITY_PUBLIC,
    YardAgent,
)

COMMANDER_AGENT_CODE = "commander"
COMMANDER_AGENT_NAME = "指挥官"
COMMANDER_EMOJI = "🧭"
#: 亲自执行时注入的技能规范条数上限（提示词预算；清单本身可以全量挂载）
SELF_EXEC_SKILL_LIMIT = 3

_PERSONA_PROMPT = (
    "你是小院的「指挥官」，也是院里的总管 Agent：既能把任务派给合适的助手，"
    "也能亲自把活干完并交付成品。"
    "你已挂载小院的全部技能与工具：需要外部事实就联网搜索/抓取网页，"
    "需要资料就检索知识库，需要成品就调用文档/图片/视频生成工具，"
    "团队缺人就用 agent.generate 一句话造一个新助手，缺技能就用 skill.generate。"
    "调用工具失败时换一种方法继续，不要编造结果，也不要把能自己解决的问题抛回给用户。"
)
_TASK_PROMPT_TEMPLATE = "完成任务：{{question}}"


def available_extensions(db: Session, yard_id: str) -> list[dict[str, Any]]:
    """当前小院可绑定的技能与插件（指挥官清单与设置页的候选集）。"""
    data = bindable.list_bindable_extensions(db, yard_id)
    return [*data.get("skills", []), *data.get("plugins", [])]


def available_tool_codes() -> list[str]:
    """工具注册表里的全部工具编码（指挥官默认全量挂载的就是这份清单）。"""
    from app.modules.tools.registry import tool_registry

    return sorted(str(tool.code) for tool in tool_registry.list_tools() if str(tool.code or ""))


def _tool_catalog() -> list[dict[str, Any]]:
    from app.modules.tools.registry import tool_registry

    rows = [
        {
            "code": str(tool.code),
            "name": str(getattr(tool, "name", "") or tool.code),
            "summary": str(getattr(tool, "description", "") or ""),
        }
        for tool in tool_registry.list_tools()
    ]
    return sorted(rows, key=lambda item: item["code"])


def _current_row(db: Session, yard_id: str) -> YardAgent | None:
    return db.scalars(
        select(YardAgent)
        .where(YardAgent.yard_id == yard_id, YardAgent.agent_code == COMMANDER_AGENT_CODE)
        .order_by(YardAgent.updated_at.desc())
        .limit(1)
    ).first()


def _free_name(db: Session, yard_id: str) -> str:
    taken = set(db.scalars(select(YardAgent.name).where(YardAgent.yard_id == yard_id)))
    if COMMANDER_AGENT_NAME not in taken:
        return COMMANDER_AGENT_NAME
    index = 2
    while f"{COMMANDER_AGENT_NAME}{index}" in taken:
        index += 1
    return f"{COMMANDER_AGENT_NAME}{index}"


def ensure_commander_agent(db: Session, *, yard_id: str, actor_id: str) -> YardAgent | None:
    """幂等落一行指挥官身份；返回 None 表示本小院暂不可用（调用方退回临时执行器）。

    已存在时不覆盖成员手工裁剪过的技能/工具——要重新全量挂载请显式调
    :func:`attach_all_bindings`（设置页的「全量挂载」按钮）。
    """
    row = _current_row(db, yard_id)
    if row is not None:
        if row.status == STATUS_DELETED:
            row.status = STATUS_PUBLISHED
            row.created_from = CREATED_FROM_COMMANDER
            row.updated_by = actor_id
            db.add(row)
            db.flush()
        return row
    skills = [str(e.get("code")) for e in available_extensions(db, yard_id) if e.get("code")]
    row = YardAgent(
        yard_id=yard_id,
        agent_code=COMMANDER_AGENT_CODE,
        name=_free_name(db, yard_id),
        description=(
            "小院指挥官：任务规划与派单，同时自带联网搜索、知识检索、文档/图片/视频生成、"
            "技能生成与自建助手等全部工具，可亲自交付成品。"
        ),
        emoji=COMMANDER_EMOJI,
        persona_prompt=_PERSONA_PROMPT,
        task_prompt_template=_TASK_PROMPT_TEMPLATE,
        allowed_tools=available_tool_codes(),
        bound_skills=skills,
        knowledge_scopes=[],
        risk_level=RISK_LOW,
        status=STATUS_PUBLISHED,
        visibility=VISIBILITY_PUBLIC,
        created_by=actor_id,
        updated_by=actor_id,
        created_from=CREATED_FROM_COMMANDER,
    )
    db.add(row)
    # 事务边界：本函数常在请求中途被调用（计划、设置页），提交会把调用方写到一半的
    # 改动一起落库，回滚更会把它们整批抹掉。用 SAVEPOINT：并发首建撞唯一约束时，
    # 只回滚这一条插入，再把对方已写入的行作为结果返回。
    savepoint = db.begin_nested()
    try:
        db.flush()
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        return _current_row(db, yard_id)
    return row


def attach_all_bindings(db: Session, *, yard_id: str, actor_id: str) -> YardAgent | None:
    """重新把全部技能与工具挂回指挥官（新增技能库后的一键补齐）。"""
    row = ensure_commander_agent(db, yard_id=yard_id, actor_id=actor_id)
    if row is None:
        return None
    row.bound_skills = [
        str(e.get("code")) for e in available_extensions(db, yard_id) if e.get("code")
    ]
    row.allowed_tools = available_tool_codes()
    row.updated_by = actor_id
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _model_view(db: Session, yard_id: str) -> dict[str, Any]:
    from app.modules.llm import binding_service

    summary = binding_service.resolution_summary(
        db, yard_id, purpose="commander_plan", agent_code=COMMANDER_AGENT_CODE
    )
    hops = summary.get("hops") or []
    head = hops[0] if hops else {}
    return {
        "supplier_id": summary.get("resolved_supplier_id"),
        "supplier_name": head.get("supplier_name"),
        "model": summary.get("resolved_model"),
        "level": summary.get("resolved_level"),
    }


def identity_payload(db: Session, *, yard_id: str) -> dict[str, Any]:
    """设置页/接口出参：指挥官是谁、挂了哪些技能与工具、现在用哪个模型。"""
    row = _current_row(db, yard_id)
    entries = {str(e.get("code")): e for e in available_extensions(db, yard_id)}
    bound_skills = [str(c) for c in ((row.bound_skills if row else []) or []) if str(c)]
    bound_tools = {str(c) for c in ((row.allowed_tools if row else []) or []) if str(c)}
    catalog = _tool_catalog()
    return {
        "agent_id": row.id if row else None,
        "agent_code": COMMANDER_AGENT_CODE,
        "name": row.name if row else COMMANDER_AGENT_NAME,
        "emoji": row.emoji if row else COMMANDER_EMOJI,
        "description": (row.description if row else "") or "",
        "status": row.status if row else "absent",
        "skills": [
            {
                "code": code,
                "name": str(entries.get(code, {}).get("name") or code.rsplit(":", 1)[-1]),
                "summary": str(entries.get(code, {}).get("summary") or ""),
                # 已下架/已删除的绑定保留展示但不参与执行，避免用户以为还挂着
                "available": code in entries,
            }
            for code in bound_skills
        ],
        "skill_count": len(bound_skills),
        "available_skill_count": len(entries),
        "tools": [{**item, "bound": item["code"] in bound_tools} for item in catalog],
        "tool_count": len(bound_tools),
        "available_tool_count": len(catalog),
        "model": _model_view(db, yard_id),
    }


def set_bindings(
    db: Session,
    *,
    yard_id: str,
    actor_id: str,
    request_id: str = "",
    bound_skills: list[str] | None = None,
    allowed_tools: list[str] | None = None,
) -> dict[str, Any]:
    """保存指挥官的技能/工具清单；未知编码一律 422，与助手编辑同口径。"""
    row = ensure_commander_agent(db, yard_id=yard_id, actor_id=actor_id)
    if row is None:
        raise AppError(
            "COMMANDER_IDENTITY_UNAVAILABLE", "未能创建指挥官身份，请稍后重试。", http_status=500
        )
    if bound_skills is not None:
        bindable.validate_bound_skills(db, yard_id, bound_skills)
        row.bound_skills = _dedup(str(c) for c in bound_skills if str(c).strip())
    if allowed_tools is not None:
        catalog = set(available_tool_codes())
        unknown = sorted({str(c) for c in allowed_tools if str(c).strip()} - catalog)
        if unknown:
            raise AppError(
                "VALIDATION_ERROR",
                f"存在未注册的工具：{unknown[0]}",
                http_status=422,
                details={"unknown_tool_codes": unknown},
            )
        row.allowed_tools = _dedup(str(c) for c in allowed_tools if str(c).strip())
    row.updated_by = actor_id
    db.add(row)
    db.commit()
    from app.modules.audit.service import log_audit

    log_audit(
        db,
        yard_id=yard_id,
        actor_id=actor_id,
        action="commander_identity_updated",
        resource_type="agent",
        resource_id=row.id,
        result="success",
        request_id=request_id,
        metadata={
            "skill_count": len(row.bound_skills or []),
            "tool_count": len(row.allowed_tools or []),
        },
    )
    return identity_payload(db, yard_id=yard_id)


def _dedup(codes: Any) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for code in codes:
        text = str(code).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def executor_bindings(
    db: Session,
    *,
    yard_id: str,
    text: str,
    limit: int = SELF_EXEC_SKILL_LIMIT,
) -> tuple[list[str], list[str]]:
    """亲自执行时取用：全量工具 + 与本子任务最相关的若干技能规范。

    技能清单可以挂上百项，但提示词只放得上几条：按子任务关键词在「技能名 + 简介」
    上打分，取最高分的 limit 条（零分不放，宁缺毋滥）。工具不做筛选，风险仍由
    工具闸按 ctx.approval_mode 把关。
    """
    row = _current_row(db, yard_id)
    if row is None:
        return [], []
    from app.modules.commander import matcher

    entries = {str(e.get("code")): e for e in available_extensions(db, yard_id)}
    tokens = matcher.keywords(text or "")
    scored: list[tuple[int, int, str]] = []
    for order, code in enumerate(str(c) for c in (row.bound_skills or []) if str(c)):
        entry = entries.get(code)
        if entry is None:
            continue
        haystack = f"{entry.get('name') or ''} {entry.get('summary') or ''}".lower()
        score = sum(1 for token in tokens if token in haystack)
        if score > 0:
            scored.append((-score, order, code))
    scored.sort()
    skills = [code for _, _, code in scored[: max(1, limit)]]
    tools = [str(c) for c in (row.allowed_tools or []) if str(c)]
    return skills, tools


def build_executor_agent(
    db: Session,
    *,
    ctx: YardContext,
    question: str,
    allow_doc_tool: bool = True,
) -> YardAgent:
    """指挥官亲自执行用的执行器（临时对象，不落库、也不改写库里的身份行）。

    有身份行时复用它的人格与技能/工具清单，模型解析同样吃「指挥官」这条绑定；
    没有身份行（建号失败等）退回旧行为：全部已注册工具、不带技能规范。
    """
    registry_tools = set(available_tool_codes())
    row = _current_row(db, ctx.yard_id)
    skills: list[str] = []
    tools: list[str] = []
    if row is not None:
        skills, tools = executor_bindings(db, yard_id=ctx.yard_id, text=question)
        tools = [code for code in tools if code in registry_tools]
    if not tools:
        tools = sorted(registry_tools)
    if not allow_doc_tool:
        tools = [code for code in tools if code != "doc.generate"]
    if row is None:
        return YardAgent(
            id=f"commander-self-{ctx.yard_id[:8]}",
            yard_id=ctx.yard_id,
            agent_code=COMMANDER_AGENT_CODE,
            name=COMMANDER_AGENT_NAME,
            description="指挥官兜底执行器：自带可用工具，必要时联网搜索、抓取与生成文档",
            emoji=COMMANDER_EMOJI,
            persona_prompt=_PERSONA_PROMPT,
            task_prompt_template=_TASK_PROMPT_TEMPLATE,
            allowed_tools=tools,
            bound_skills=[],
            knowledge_scopes=[],
            status=STATUS_PUBLISHED,
            visibility=VISIBILITY_PUBLIC,
            created_by=ctx.user_id,
            created_from=CREATED_FROM_COMMANDER,
        )
    # 新建 transient 实例：直接复用 row 会让 top-k 技能写回库里那份全量清单
    return YardAgent(
        id=row.id,
        yard_id=row.yard_id,
        agent_code=row.agent_code,
        name=row.name,
        description=row.description,
        emoji=row.emoji,
        persona_prompt=row.persona_prompt,
        task_prompt_template=row.task_prompt_template,
        allowed_tools=tools,
        bound_skills=skills,
        knowledge_scopes=list(row.knowledge_scopes or []),
        supplier_id=row.supplier_id,
        model_name=row.model_name,
        status=STATUS_PUBLISHED,
        visibility=row.visibility,
        version=row.version,
        created_by=row.created_by,
        created_from=CREATED_FROM_COMMANDER,
    )
