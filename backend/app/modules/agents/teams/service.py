"""班组服务层：定义 CRUD + 顺序接力执行 + 成员级断点续跑 + 成员与班组 Token 消耗统计（W2）。"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.agents import declarative
from app.modules.agents import run_steps as rs
from app.modules.agents.declarative import STATUS_PUBLISHED
from app.modules.agents.teams.models import (
    MEMBER_ERROR,
    MEMBER_OK,
    MEMBER_PENDING,
    MEMBER_RUNNING,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_QUEUED,
    RUN_RUNNING,
    AgentTeam,
    TeamRun,
    TeamRunMember,
)
from app.modules.audit.service import log_audit
from app.modules.llm.models import LLMTrace

log = get_logger(__name__)

MEMBER_MIN, MEMBER_MAX = 2, 8
MEMBER_OUTPUT_CAP = 4000
INPUT_PREVIEW_CAP = 2000

CRITIC_DIRECTIVE = (
    "\n\n【审核要求】你是本班组指派的审核员（critic）：请审视上面的原始问题与前序"
    "成员的全部输出，逐条指出事实错误、依据不足与可行性问题；随后给出你的修订结论"
    "（若确无需修订，也要明确说明「审核通过，无需修订」）。你的结论将并入班组最终答案。"
)

CRITIC_KEYWORDS = (
    "审核",
    "审查",
    "审阅",
    "评审",
    "审校",
    "校对",
    "复核",
    "把关",
    "验收",
    "质检",
    "风控",
    "critic",
    "review",
    "audit",
    "verify",
    "validate",
    "guard",
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# 1) 成员定义规整与拼接纯函数（可单测）
# ---------------------------------------------------------------------------


class TeamMemberSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1, max_length=36)
    role_name: str = Field(min_length=1, max_length=100)
    instruction: str = Field(default="", max_length=4000)
    is_critic: bool = False

    @field_validator("agent_id", "role_name", "instruction")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("role_name")
    @classmethod
    def _role_not_blank(cls, v: str) -> str:
        if not v:
            raise ValueError("role_name 不能为空白")
        return v


def _clip(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    return text[:cap] + "…（内容过长，已截断）"


def parse_members(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise AppError("TEAM_MEMBERS_INVALID", "members 必须为数组。", http_status=400)
    specs: list[TeamMemberSpec] = []
    try:
        for entry in raw:
            specs.append(TeamMemberSpec.model_validate(entry))
    except ValidationError as e:
        errors: list[dict[str, Any]] = [dict(x) for x in e.errors()]
        first: dict[str, Any] = errors[0] if errors else {}
        loc = ".".join(str(p) for p in first.get("loc", ()))
        raise AppError(
            "TEAM_MEMBERS_INVALID",
            f"班组成员定义不合法（{loc or 'members'}）：{first.get('msg', '格式错误')}",
            http_status=400,
        ) from None
    if not MEMBER_MIN <= len(specs) <= MEMBER_MAX:
        raise AppError(
            "TEAM_MEMBER_COUNT",
            f"班组成员数须在 {MEMBER_MIN}-{MEMBER_MAX} 个之间（当前 {len(specs)} 个）。",
            http_status=400,
        )
    return [s.model_dump() for s in specs]


def is_review_role(*texts: str) -> bool:
    joined = " ".join(t for t in texts if t).lower()
    return any(k in joined for k in CRITIC_KEYWORDS)


def members_from_blueprint(items: list[dict[str, Any]], ok_codes: set[str]) -> list[dict[str, Any]]:
    ordered = [i for i in items if str(i.get("agent_code")) in ok_codes and i.get("agent_id")]
    members: list[dict[str, Any]] = []
    for idx, it in enumerate(ordered):
        goal = str(it.get("description") or "").strip()
        persona = str(it.get("persona_prompt") or "").strip()
        summary = goal if goal else persona[:200]
        role_name = str(it.get("name") or "").strip() or f"成员{idx + 1}"
        members.append(
            {
                "agent_id": str(it["agent_id"]),
                "role_name": role_name[:100],
                "instruction": summary[:4000],
                "is_critic": False,
            }
        )
    if members:
        last = ordered[-1]
        if is_review_role(
            str(last.get("name") or ""),
            str(last.get("description") or ""),
            str(last.get("persona_prompt") or ""),
        ):
            members[-1]["is_critic"] = True
    return members


def build_member_question(
    original: str,
    prior: Iterable[tuple[str, str]],
    instruction: str,
    is_critic: bool,
) -> str:
    parts: list[str] = [original.strip()]
    prior_list = [(role, output) for role, output in prior]
    if prior_list:
        block = "\n".join(
            f"【{role}】：{_clip(output, MEMBER_OUTPUT_CAP)}" for role, output in prior_list
        )
        parts.append("前序成员接力输出：\n" + block)
    if instruction:
        parts.append(f"你的班组任务：{instruction}")
    question = "\n\n".join(parts)
    if is_critic:
        question += CRITIC_DIRECTIVE
    return question


def compose_answer(parts: list[tuple[str, bool, str]]) -> str:
    if not parts:
        return ""
    _, last_critic, last_out = parts[-1]
    if last_critic:
        return last_out
    critic_bits = [f"【审核意见·{role}】\n{out}" for role, is_c, out in parts[:-1] if is_c]
    if not critic_bits:
        return last_out
    return last_out + "\n\n---\n" + "\n\n".join(critic_bits)


# ---------------------------------------------------------------------------
# 2) 班组定义 CRUD（全部带小院边界）
# ---------------------------------------------------------------------------


def _clean_name(raw: Any) -> str:
    name = str(raw or "").strip()
    if not name:
        raise AppError("INVALID_REQUEST", "班组名称不能为空。", http_status=400)
    return name[:100]


def _require_name_free(
    db: Session, yard_id: str, name: str, *, exclude_id: str | None = None
) -> None:
    dup = db.scalar(select(AgentTeam).where(AgentTeam.yard_id == yard_id, AgentTeam.name == name))
    if dup is not None and dup.id != exclude_id:
        raise AppError("TEAM_NAME_CONFLICT", f"本小院已存在同名班组：{name}。", http_status=409)


def _validate_member_agents(db: Session, yard_id: str, members: list[dict[str, Any]]) -> None:
    for m in members:
        declarative.get_agent(db, yard_id=yard_id, agent_id=str(m["agent_id"]))


def _audit_team(
    db: Session, *, yard_id: str, actor_id: str, action: str, team: AgentTeam, request_id: str
) -> None:
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=actor_id,
        action=action,
        resource_type="agent_team",
        resource_id=team.id,
        result="success",
        request_id=request_id,
        metadata={"name": team.name, "member_count": len(team.members_json or [])},
    )


def get_team(db: Session, *, yard_id: str, team_id: str) -> AgentTeam:
    team = db.scalar(select(AgentTeam).where(AgentTeam.id == team_id, AgentTeam.yard_id == yard_id))
    if team is None:
        raise AppError("TEAM_NOT_FOUND", f"班组不存在：{team_id}。", http_status=404)
    return team


def list_teams(db: Session, *, yard_id: str) -> list[AgentTeam]:
    return list(
        db.scalars(
            select(AgentTeam)
            .where(AgentTeam.yard_id == yard_id)
            .order_by(AgentTeam.created_at.desc(), AgentTeam.id.desc())
        )
    )


def create_team(
    db: Session,
    *,
    yard_id: str,
    actor_id: str,
    name: Any,
    members: Any,
    model_hint: str | None = None,
    description: str | None = None,
    enabled: bool = True,
    request_id: str,
) -> AgentTeam:
    clean = _clean_name(name)
    specs = parse_members(members)
    _validate_member_agents(db, yard_id, specs)
    _require_name_free(db, yard_id, clean)
    team = AgentTeam(
        yard_id=yard_id,
        name=clean,
        model_hint=(str(model_hint).strip()[:100] or None) if model_hint else None,
        description=(str(description).strip()[:4000] or None) if description else None,
        members_json=specs,
        enabled=bool(enabled),
        created_by=actor_id,
    )
    db.add(team)
    db.flush()
    _audit_team(
        db,
        yard_id=yard_id,
        actor_id=actor_id,
        action="team_created",
        team=team,
        request_id=request_id,
    )
    return team


def update_team(
    db: Session,
    *,
    yard_id: str,
    actor_id: str,
    team_id: str,
    fields: dict[str, Any],
    request_id: str,
) -> AgentTeam:
    team = get_team(db, yard_id=yard_id, team_id=team_id)
    if not fields:
        raise AppError("INVALID_REQUEST", "没有需要更新的字段。", http_status=400)
    allowed = ("name", "description", "model_hint", "enabled", "members")
    for key in fields:
        if key not in allowed:
            raise AppError("INVALID_REQUEST", f"不支持更新的字段：{key}", http_status=400)
    if "name" in fields:
        clean = _clean_name(fields["name"])
        _require_name_free(db, yard_id, clean, exclude_id=team.id)
        team.name = clean
    if "description" in fields:
        raw_desc = fields["description"]
        team.description = (str(raw_desc).strip()[:4000] or None) if raw_desc else None
    if "model_hint" in fields:
        raw_hint = fields["model_hint"]
        team.model_hint = (str(raw_hint).strip()[:100] or None) if raw_hint else None
    if "enabled" in fields:
        team.enabled = bool(fields["enabled"])
    if "members" in fields:
        specs = parse_members(fields["members"])
        _validate_member_agents(db, yard_id, specs)
        team.members_json = specs
    _audit_team(
        db,
        yard_id=yard_id,
        actor_id=actor_id,
        action="team_updated",
        team=team,
        request_id=request_id,
    )
    db.flush()
    return team


def delete_team(
    db: Session, *, yard_id: str, actor_id: str, team_id: str, request_id: str
) -> AgentTeam:
    team = get_team(db, yard_id=yard_id, team_id=team_id)
    _audit_team(
        db,
        yard_id=yard_id,
        actor_id=actor_id,
        action="team_deleted",
        team=team,
        request_id=request_id,
    )
    db.delete(team)
    db.flush()
    return team


# ---------------------------------------------------------------------------
# 3) 序列化（含成员级与班组级 Token 统计）
# ---------------------------------------------------------------------------


def _iso(dt: Any) -> str | None:
    if isinstance(dt, datetime) and dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat() if dt is not None else None


def team_out(team: AgentTeam) -> dict[str, Any]:
    return {
        "id": team.id,
        "yard_id": team.yard_id,
        "name": team.name,
        "model_hint": team.model_hint,
        "description": team.description,
        "members": [dict(m) for m in (team.members_json or []) if isinstance(m, dict)],
        "enabled": bool(team.enabled),
        "schedule": copy.deepcopy(team.schedule_json),
        "next_run_at": _iso(team.next_run_at),
        "created_by": team.created_by,
        "created_at": _iso(team.created_at),
        "updated_at": _iso(team.updated_at),
    }


def _tokens_by_task_ids(
    db: Session, yard_id: str, task_ids: list[str]
) -> dict[str, dict[str, Any]]:
    if not task_ids:
        return {}
    rows = db.execute(
        select(
            LLMTrace.run_id,
            func.coalesce(func.sum(LLMTrace.input_tokens), 0),
            func.coalesce(func.sum(LLMTrace.output_tokens), 0),
            func.max(LLMTrace.model),
        )
        .where(LLMTrace.yard_id == yard_id, LLMTrace.run_id.in_(task_ids))
        .group_by(LLMTrace.run_id)
    ).all()
    out: dict[str, dict[str, Any]] = {}
    for tid, in_tok, out_tok, model_name in rows:
        if not tid:
            continue
        i_val = int(in_tok or 0)
        o_val = int(out_tok or 0)
        out[str(tid)] = {
            "input_tokens": i_val,
            "output_tokens": o_val,
            "total_tokens": i_val + o_val,
            "model": str(model_name or ""),
        }
    return out


def member_out(row: TeamRunMember, token_usage: dict[str, Any] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "index": int(row.index),
        "role_name": row.role_name,
        "member_agent_id": row.member_agent_id,
        "task_id": row.task_id,
        "status": row.status,
        "input_preview": row.input_preview,
        "output": row.output,
        "error_text": row.error_text,
        "updated_at": _iso(row.updated_at),
    }
    if token_usage is not None:
        data["token_usage"] = token_usage
    return data


def run_out(
    run: TeamRun,
    members: list[TeamRunMember] | None = None,
    db: Session | None = None,
) -> dict[str, Any]:
    state = getattr(run, "_sa_instance_state", None)
    session = db or (state.session if state is not None else None)
    tok_map: dict[str, dict[str, Any]] = {}
    if session is not None:
        tids = [m.task_id for m in (members or []) if m.task_id]
        tids.append(f"team-summary-{run.id}")
        tok_map = _tokens_by_task_ids(session, run.yard_id, tids)

    total_in = sum(v["input_tokens"] for v in tok_map.values())
    total_out = sum(v["output_tokens"] for v in tok_map.values())

    data: dict[str, Any] = {
        "id": run.id,
        "team_id": run.team_id,
        "yard_id": run.yard_id,
        "team_name": run.team_name,
        "created_by": run.created_by,
        "trigger": run.trigger,
        "output_format": run.output_format,
        "research": copy.deepcopy(run.research_json),
        "document_available": run.status == RUN_COMPLETED and bool((run.answer or "").strip()),
        "task_id": run.task_id,
        "input": run.input,
        "status": run.status,
        "answer": run.answer,
        "current_member": int(run.current_member),
        "last_error": run.last_error,
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "created_at": _iso(run.created_at),
        "updated_at": _iso(run.updated_at),
        "member_count": len(run.members_json or []),
        "token_usage": {
            "input_tokens": total_in,
            "output_tokens": total_out,
            "total_tokens": total_in + total_out,
        },
    }
    if members is not None:
        data["members"] = [member_out(m, tok_map.get(m.task_id or "")) for m in members]
    return data


def run_summary(run: TeamRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "team_id": run.team_id,
        "team_name": run.team_name,
        "created_by": run.created_by,
        "trigger": run.trigger,
        "output_format": run.output_format,
        "status": run.status,
        "answer_preview": (run.answer or "")[:300],
        "input_preview": run.input[:120],
        "current_member": int(run.current_member),
        "member_count": len(run.members_json or []),
        "last_error": (str(run.last_error)[:300] if run.last_error else None),
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "created_at": _iso(run.created_at),
    }


# ---------------------------------------------------------------------------
# 4) 运行：接力 + 断点续跑
# ---------------------------------------------------------------------------


def require_run_access(run: TeamRun, ctx: YardContext) -> None:
    if run.yard_id != ctx.yard_id or (run.created_by and run.created_by != ctx.user_id):
        raise AppError("TEAM_RUN_NOT_FOUND", "班组运行不存在或无权访问。", http_status=404)


def get_team_run(db: Session, *, yard_id: str, run_id: str, user_id: str | None = None) -> TeamRun:
    run = db.scalar(
        select(TeamRun)
        .where(TeamRun.id == run_id, TeamRun.yard_id == yard_id)
        .execution_options(populate_existing=True)
    )
    if run is None or (run.created_by and run.created_by != user_id):
        raise AppError("TEAM_RUN_NOT_FOUND", "班组运行不存在或无权访问。", http_status=404)
    return run


def list_team_runs(
    db: Session, *, yard_id: str, team_id: str, user_id: str | None = None
) -> list[TeamRun]:
    return list(
        db.scalars(
            select(TeamRun)
            .where(
                TeamRun.yard_id == yard_id,
                TeamRun.team_id == team_id,
                or_(TeamRun.created_by.is_(None), TeamRun.created_by == user_id),
            )
            .order_by(TeamRun.created_at.desc(), TeamRun.id.desc())
        )
    )


def paged_team_runs(
    db: Session,
    *,
    ctx: YardContext,
    team_id: str | None = None,
    status: str | None = None,
    trigger: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    filters = [
        TeamRun.yard_id == ctx.yard_id,
        or_(TeamRun.created_by.is_(None), TeamRun.created_by == ctx.user_id),
    ]
    for column, value in (
        (TeamRun.team_id, team_id),
        (TeamRun.status, status),
        (TeamRun.trigger, trigger),
    ):
        if value is not None:
            filters.append(column == value)
    total = db.scalar(select(func.count()).select_from(TeamRun).where(*filters)) or 0
    runs = db.scalars(
        select(TeamRun)
        .where(*filters)
        .order_by(TeamRun.created_at.desc(), TeamRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "runs": [run_summary(r) for r in runs],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def execution_context(db: Session, *, yard_id: str, user_id: str, request_id: str) -> YardContext:
    from app.modules.auth.models import User
    from app.modules.yards.models import YardMembership

    user = db.get(User, user_id, populate_existing=True)
    membership = db.scalar(
        select(YardMembership)
        .where(
            YardMembership.yard_id == yard_id,
            YardMembership.user_id == user_id,
            YardMembership.status == "active",
        )
        .execution_options(populate_existing=True)
    )
    if user is None or user.status != "active" or membership is None:
        raise AppError(
            "TEAM_EXECUTOR_UNAVAILABLE", "执行者已停用或不再是有效小院成员。", http_status=403
        )
    return YardContext(
        yard_id=yard_id, user_id=user_id, roles=[membership.role], request_id=request_id
    )


def require_team_idle(db: Session, team: AgentTeam, *, exclude_run_id: str = "") -> None:
    # UPDATE 持有班组行锁直到台账提交，与调度 CAS 共用同一串行化边界。
    locked = db.execute(
        update(AgentTeam)
        .where(
            AgentTeam.id == team.id,
            AgentTeam.yard_id == team.yard_id,
            AgentTeam.enabled.is_(True),
        )
        .values(enabled=True)
        .execution_options(synchronize_session=False)
    )
    if not locked.rowcount:  # type: ignore[attr-defined]
        raise AppError("TEAM_DISABLED", "班组已停用，不能运行。", http_status=409)
    busy = db.scalar(
        select(TeamRun.id)
        .where(
            TeamRun.team_id == team.id,
            TeamRun.yard_id == team.yard_id,
            TeamRun.status.in_((RUN_QUEUED, RUN_RUNNING)),
            TeamRun.id != exclude_run_id,
        )
        .limit(1)
    )
    if busy:
        raise AppError("TEAM_BUSY", "班组已有排队或运行中的任务，本次不并发执行。", http_status=409)


def members_of_run(db: Session, run: TeamRun) -> list[TeamRunMember]:
    return list(
        db.scalars(
            select(TeamRunMember)
            .where(TeamRunMember.team_run_id == run.id)
            .order_by(TeamRunMember.index.asc())
        )
    )


def require_members_runnable(
    db: Session, *, yard_id: str, members: list[dict[str, Any]], user_id: str | None = None
) -> None:
    for m in members:
        agent = declarative.get_agent(db, yard_id=yard_id, agent_id=str(m.get("agent_id") or ""))
        if agent.visibility == "private" and (not user_id or agent.created_by != user_id):
            raise AppError("AGENT_NOT_FOUND", "成员不存在或无权使用。", http_status=404)
        if agent.status != STATUS_PUBLISHED:
            raise AppError(
                "TEAM_MEMBER_NOT_PUBLISHED",
                f"班组成员「{m.get('role_name')}」（{agent.agent_code}）当前状态为 "
                f"{agent.status}，需先发布为 published 才能参与接力。",
                http_status=400,
            )


def create_team_run(
    db: Session,
    *,
    team: AgentTeam,
    user_input: str,
    ctx: YardContext,
    output_format: str = "text",
    trigger: str = "manual",
) -> TeamRun:
    if team.yard_id != ctx.yard_id:
        raise AppError("TEAM_NOT_FOUND", "班组不存在。", http_status=404)
    if (
        not user_input.strip()
        or len(user_input) > 8000
        or output_format not in {"text", "md", "docx"}
    ):
        raise AppError("INVALID_REQUEST", "运行输入或输出格式不合法。", http_status=400)
    specs = parse_members(team.members_json)
    require_members_runnable(db, yard_id=ctx.yard_id, members=specs, user_id=ctx.user_id)
    require_team_idle(db, team)
    run = TeamRun(
        team_id=team.id,
        yard_id=ctx.yard_id,
        created_by=ctx.user_id,
        team_name=team.name,
        trigger=trigger,
        output_format=output_format,
        input=user_input.strip(),
        status=RUN_QUEUED,
        members_json=copy.deepcopy(specs),
        current_member=0,
    )
    db.add(run)
    db.flush()
    for idx, spec in enumerate(specs):
        db.add(
            TeamRunMember(
                team_run_id=run.id,
                index=idx,
                member_agent_id=str(spec["agent_id"]),
                role_name=str(spec["role_name"]),
                status=MEMBER_PENDING,
            )
        )
    db.flush()
    return run


def create_run_task(db: Session, *, run: TeamRun, ctx: YardContext):
    from app.modules.tasks.service import TaskService

    require_run_access(run, ctx)
    task = TaskService.create(
        db, ctx, task_type="team_run", input={"run_id": run.id}, request_id=ctx.request_id
    )
    run.task_id = task.id
    db.flush()
    return task


def dispatch_run(db: Session, run: TeamRun, task: Any) -> None:
    from app.modules.tasks.router import _enqueue_or_inline
    from app.modules.tasks.service import TaskService

    _enqueue_or_inline(db, task)
    if (task.input_json or {}).get("meta", {}).get("enqueue_failed"):
        failed = db.execute(
            update(TeamRun)
            .where(
                TeamRun.id == run.id,
                TeamRun.status == RUN_QUEUED,
            )
            .values(
                status=RUN_FAILED,
                finished_at=_utcnow(),
                last_error="TEAM_ENQUEUE_FAILED: 任务投递失败，请重试。",
            )
            .execution_options(synchronize_session=False)
        )
        if failed.rowcount:  # type: ignore[attr-defined]
            db.refresh(task)
            if task.status == "queued":
                TaskService.set_status(
                    db,
                    task,
                    "cancelled",
                    error_code="TEAM_ENQUEUE_FAILED",
                    error_message="任务投递失败，请从班组运行记录重试。",
                )
        db.commit()


def prepare_retry(db: Session, *, run: TeamRun, ctx: YardContext):
    require_run_access(run, ctx)
    if run.status == RUN_COMPLETED:
        raise AppError("TEAM_RUN_ALREADY_COMPLETED", "运行已完成，不允许重放。", http_status=409)
    if run.status != RUN_FAILED:
        raise AppError("TEAM_RUN_STATE_CONFLICT", "运行尚未结束，不能重复续跑。", http_status=409)
    current = execution_context(
        db, yard_id=run.yard_id, user_id=run.created_by or ctx.user_id, request_id=ctx.request_id
    )
    team = get_team(db, yard_id=run.yard_id, team_id=run.team_id)
    require_team_idle(db, team, exclude_run_id=run.id)
    require_members_runnable(
        db, yard_id=run.yard_id, members=run.members_json, user_id=current.user_id
    )
    claimed = db.execute(
        update(TeamRun)
        .where(
            TeamRun.id == run.id,
            TeamRun.status == RUN_FAILED,
        )
        .values(status=RUN_QUEUED, last_error=None, finished_at=None)
        .execution_options(synchronize_session=False)
    )
    if not claimed.rowcount:  # type: ignore[attr-defined]
        raise AppError("TEAM_RUN_STATE_CONFLICT", "运行状态已变化。", http_status=409)
    db.refresh(run)
    return create_run_task(db, run=run, ctx=current)


def mark_run_failed(db: Session, run: TeamRun, detail: str) -> None:
    run.status = RUN_FAILED
    run.last_error = detail[:2000]
    run.finished_at = _utcnow()
    db.commit()


#: 成员各自的声明式检索看到的只是接力后的长问句；知识库状态由班组共享检索统一说明，
#: 避免“已展示引用”与“暂无命中”同时出现在一次运行里。
_MEMBER_KB_STATE_WARNINGS = {
    "知识库暂无命中，回答不包含引用。",
    "知识库检索暂不可用，本次回答未包含引用。",
}
#: 共享证据上限：成员各自的引用可能重复或超量，必须给最终综合提示词留出上下文预算。
_MAX_KNOWLEDGE_SOURCES = 10


def _source_key(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("chunk_id") or item.get("document_id") or item.get("quote") or item)
    return str(item)


def merge_research(run: TeamRun, citations: list, warnings: list) -> None:
    research = copy.deepcopy(run.research_json or {})
    for key, values in (("knowledge", citations), ("warnings", warnings)):
        items = research.setdefault(key, [])
        known = {_source_key(existing) for existing in items}
        for item in values:
            if key == "warnings" and item in _MEMBER_KB_STATE_WARNINGS:
                continue
            if key == "knowledge":
                marker = _source_key(item)
                if marker in known:
                    continue
                if len(items) >= _MAX_KNOWLEDGE_SOURCES:
                    continue
                known.add(marker)
            elif item in items:
                continue
            items.append(item)
    run.research_json = research


def _harvest_completed_answer(steps: list[rs.AgentRunStep]) -> str | None:
    for step in steps:
        if step.step_type == rs.STEP_FINAL and step.status == rs.STEP_OK:
            return str((step.output_json or {}).get("answer") or "")
    return None


async def _run_member(
    db: Session,
    *,
    ctx: YardContext,
    run: TeamRun,
    row: TeamRunMember,
    spec: dict[str, Any],
    prior: list[tuple[str, str]],
) -> str:
    from app.modules.agents.teams.research import research_context

    require_members_runnable(db, yard_id=ctx.yard_id, members=[spec], user_id=ctx.user_id)
    agent = declarative.get_agent(db, yard_id=ctx.yard_id, agent_id=row.member_agent_id)
    question = build_member_question(
        run.input, prior, str(spec.get("instruction") or ""), bool(spec.get("is_critic"))
    )
    if not row.task_id:
        row.task_id = f"team-{uuid.uuid4().hex[:16]}"
    row.status = MEMBER_RUNNING
    row.input_preview = question[:INPUT_PREVIEW_CAP]
    row.error_text = None
    run.current_member = int(row.index)
    db.commit()

    task_id = str(row.task_id)
    steps = rs.list_steps(db, yard_id=ctx.yard_id, run_id=task_id)
    harvested = _harvest_completed_answer(steps)
    if steps and harvested is not None:
        for step in steps:
            meta = step.input_json or {}
            merge_research(run, meta.get("citations") or [], meta.get("warnings") or [])
        return harvested
    resume = bool(steps)
    result = await declarative.run_declarative_agent(
        db,
        agent=agent,
        input_data={} if resume else {"question": question},
        ctx=ctx,
        task_id=task_id,
        resume_run_id=task_id if resume else None,
        context=research_context(run.research_json),
    )
    merge_research(run, result.citations or [], result.warnings or [])
    return str(result.output.get("answer") or "")


def _mark_member_failed(db: Session, run: TeamRun, row: TeamRunMember, detail: str) -> None:
    row.status = MEMBER_ERROR
    row.error_text = detail[:2000]
    run.status = RUN_FAILED
    run.last_error = detail[:2000]
    run.finished_at = _utcnow()
    db.commit()
    from app.modules.push import service as push_service

    team = db.get(AgentTeam, run.team_id)
    team_name = team.name if team is not None else run.team_id
    push_service.dispatch(
        db,
        yard_id=run.yard_id,
        event_type="team_run_failed",
        payload={
            "title": f"班组「{team_name}」运行失败",
            "team_name": team_name,
            "member": row.role_name,
            "error": detail[:200],
        },
        request_id="",
    )
    db.commit()


def _wrap_member_error(run: TeamRun, row: TeamRunMember, exc: AppError) -> AppError:
    return AppError(
        exc.code,
        exc.message,
        http_status=exc.http_status,
        details={**(exc.details or {}), "team_run_id": run.id, "member_index": int(row.index)},
    )


async def continue_team_run(db: Session, *, run: TeamRun, ctx: YardContext) -> TeamRun:
    require_run_access(run, ctx)
    claimed = db.execute(
        update(TeamRun)
        .where(
            TeamRun.id == run.id,
            TeamRun.yard_id == ctx.yard_id,
            TeamRun.status == RUN_QUEUED,
        )
        .values(status=RUN_RUNNING, started_at=_utcnow(), finished_at=None, last_error=None)
        .execution_options(synchronize_session=False)
    )
    if not claimed.rowcount:  # type: ignore[attr-defined]
        db.rollback()
        raise AppError("TEAM_RUN_STATE_CONFLICT", "运行已被领取或已结束。", http_status=409)
    db.commit()
    db.refresh(run)
    try:
        from app.modules.agents.teams.artifacts import build_document
        from app.modules.agents.teams.research import collect_research, synthesize_answer

        team = get_team(db, yard_id=run.yard_id, team_id=run.team_id)
        if not team.enabled:
            raise AppError("TEAM_DISABLED", "班组已停用。", http_status=409)
        if run.trigger == "schedule" and not (team.schedule_json or {}).get("enabled"):
            raise AppError("TEAM_SCHEDULE_DISABLED", "计划已禁用。", http_status=409)
        specs = parse_members(run.members_json)
        require_members_runnable(db, yard_id=ctx.yard_id, members=specs, user_id=ctx.user_id)
        rows = members_of_run(db, run)
        if len(rows) != len(specs) or [r.index for r in rows] != list(range(len(specs))):
            raise AppError(
                "TEAM_RUN_CHECKPOINT_INVALID", "运行快照与成员台账不一致。", http_status=409
            )
        if run.research_json is None:
            run.research_json = await collect_research(db, ctx=ctx, question=run.input)
            db.commit()
        for row in rows:
            if row.status == MEMBER_OK:
                continue
            idx = int(row.index)
            spec = specs[idx]
            prior = [
                (r.role_name, r.output or "")
                for r in rows
                if int(r.index) < idx and r.status == MEMBER_OK
            ]
            try:
                answer = await _run_member(db, ctx=ctx, run=run, row=row, spec=spec, prior=prior)
            except AppError as exc:
                db.rollback()
                _mark_member_failed(db, run, row, f"{exc.code}: {exc.message}")
                raise _wrap_member_error(run, row, exc) from exc
            except Exception as exc:
                db.rollback()
                detail = f"{type(exc).__name__}: {exc}"
                _mark_member_failed(db, run, row, detail)
                raise AppError(
                    "TEAM_RUN_MEMBER_FAILED",
                    detail,
                    http_status=502,
                    details={"team_run_id": run.id, "member_index": idx},
                ) from exc
            row.output = answer
            row.status = MEMBER_OK
            row.error_text = None
            run.current_member = idx + 1
            db.commit()
        parts = [
            (r.role_name, bool(specs[int(r.index)].get("is_critic")), r.output or "") for r in rows
        ]
        run.answer = await synthesize_answer(db, ctx=ctx, run=run, parts=parts)
        if not run.answer or not run.answer.strip():
            raise AppError("TEAM_SUMMARY_EMPTY", "汇总模型未返回答案。", http_status=502)
        if run.output_format != "text":
            run.document_json = build_document(run)
        run.status = RUN_COMPLETED
        run.finished_at = _utcnow()
        db.commit()
        return run
    except BaseException as exc:
        db.rollback()
        detail = f"{exc.code}: {exc.message}" if isinstance(exc, AppError) else str(exc)
        mark_run_failed(db, run, detail or type(exc).__name__)
        raise
