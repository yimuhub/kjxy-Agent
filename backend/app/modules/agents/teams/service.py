"""班组服务层：定义 CRUD + 顺序接力执行 + 成员级断点续跑（W2）。

接力协议（run_team 时序）：
1. 建 ``TeamRun``（status=running）+ 每成员一行 ``TeamRunMember``（pending），
   同时把班组成员定义快照进 ``run.members_json``——班组事后编辑不影响续跑。
2. 成员按序执行：member[i] 的接力输入 = 原始问题 + 前序各成员输出
   （每段格式「【{role_name}】：{output}」，逐段截断）+ 该成员 instruction
   +（is_critic 时）审核指令；整段作为声明式 run 的 ``question``。
3. **先落 task_id 再执行**：每成员预置 ``team_run_members.task_id`` 并提交，
   随后声明式 run 以该 id 为 run_id —— 崩溃/失败后按 W1 的
   ``agent_run_steps`` checkpoint 续跑（已 ok 工具步不重放副作用；失败 LLM
   步原位重跑）。无工具成员走单轮旧路径无步骤行，重试时整段重跑（该路径
   没有副作用工具，重跑安全）。
4. 每成员完成即 commit（步边界释放写事务，与 W1 口径一致）；任一成员失败
   → 成员 error + 班组 run failed（last_error），停在断点并抛原始错误。
5. 答案拼接（``compose_answer``）：末位成员输出为答案主体——
   ▷ 末位即 critic → 答案 = 其输出（修订结论即最终结论）；
   ▷ 末位非 critic 而队列中有 critic → 答案 = 末位输出 + 「---」分隔 +
     逐条「【审核意见·{role_name}】」并入。critic 的输出同样并入后续成员的
     接力上下文（其「修订结论」可被后续成员继续加工）。

重试口径（retry）：已 ok 成员**绝不重跑**（直接复用 output）；从首个非 ok
成员续跑（running 崩溃残留同样视为未完成）；该成员若已有 checkpoint 步骤
则走 resume（不重放副作用），否则整段重跑。
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import TenantContext
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
    RUN_RUNNING,
    AgentTeam,
    TeamRun,
    TeamRunMember,
)
from app.modules.audit.service import log_audit

log = get_logger(__name__)

#: 班组成员数量硬边界（与迁移注释、工厂装配一致）
MEMBER_MIN, MEMBER_MAX = 2, 8
#: 接力上下文中单个成员输出的截断上限（护栏：防上下文无界膨胀）
MEMBER_OUTPUT_CAP = 4000
#: team_run_members.input_preview 存储截断上限
INPUT_PREVIEW_CAP = 2000

#: critic 成员的审核指令（拼进该成员的接力 question）
CRITIC_DIRECTIVE = (
    "\n\n【审核要求】你是本班组指派的审核员（critic）：请审视上面的原始问题与前序"
    "成员的全部输出，逐条指出事实错误、依据不足与可行性问题；随后给出你的修订结论"
    "（若确无需修订，也要明确说明「审核通过，无需修订」）。你的结论将并入班组最终答案。"
)

#: 审核类角色识别关键词（工厂蓝图自动标 is_critic 的启发式词表）
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
    """班组成员定义：agent_id 引用本租户声明式 Agent。extra=forbid。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1, max_length=36)
    role_name: str = Field(min_length=1, max_length=100)
    #: 该角色在班组内的接力须知（拼进接力 question）
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
    """按上限截断并诚实标注（不静默丢内容）。"""
    if len(text) <= cap:
        return text
    return text[:cap] + "…（内容过长，已截断）"


def parse_members(raw: Any) -> list[dict[str, Any]]:
    """members 原始数组 → 规整成员定义列表（保持顺序）。

    - 数量越界 → 400 TEAM_MEMBER_COUNT；
    - 字段非法/携带未知字段 → 400 TEAM_MEMBERS_INVALID。
    """
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
    """审核类角色启发式识别：名称/描述/persona 命中任一关键词（大小写不敏感）。"""
    joined = " ".join(t for t in texts if t).lower()
    return any(k in joined for k in CRITIC_KEYWORDS)


def members_from_blueprint(items: list[dict[str, Any]], ok_codes: set[str]) -> list[dict[str, Any]]:
    """工厂蓝图角色 → 班组成员定义（按蓝图顺序，W2 工厂接入）。

    - 只取 ``ok_codes`` 内且已装配出 ``agent_id`` 的角色；
    - role_name = 蓝图角色名；instruction = 角色目标（description）摘要，
      缺失时回落 persona_prompt 前 200 字（goal/backstory 摘要口径）；
    - 末位角色若命中审核类关键词 → 自动 is_critic=true。
    """
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
    """构造 member[i] 的接力输入（声明式 run 的 question）。"""
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
    """最终答案拼接：(role_name, is_critic, output) 有序列表 → 答案文本。

    策略见模块 docstring 第 5 条：末位为主体；末位非 critic 且队列中有 critic
    时，把 critic 意见以「【审核意见·角色】」段落并入。
    """
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
# 2) 班组定义 CRUD（全部带租户边界）
# ---------------------------------------------------------------------------


def _clean_name(raw: Any) -> str:
    name = str(raw or "").strip()
    if not name:
        raise AppError("INVALID_REQUEST", "班组名称不能为空。", http_status=400)
    return name[:100]


def _require_name_free(
    db: Session, tenant_id: str, name: str, *, exclude_id: str | None = None
) -> None:
    dup = db.scalar(
        select(AgentTeam).where(AgentTeam.tenant_id == tenant_id, AgentTeam.name == name)
    )
    if dup is not None and dup.id != exclude_id:
        raise AppError("TEAM_NAME_CONFLICT", f"本租户已存在同名班组：{name}。", http_status=409)


def _validate_member_agents(db: Session, tenant_id: str, members: list[dict[str, Any]]) -> None:
    """成员 agent 必须存在且属于本租户（跨租户引用一律 404，防枚举）。"""
    for m in members:
        declarative.get_agent(db, tenant_id=tenant_id, agent_id=str(m["agent_id"]))


def _audit_team(
    db: Session, *, tenant_id: str, actor_id: str, action: str, team: AgentTeam, request_id: str
) -> None:
    """班组 CRUD 审计；永不影响主流程。"""
    log_audit(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=action,
        resource_type="agent_team",
        resource_id=team.id,
        result="success",
        request_id=request_id,
        metadata={"name": team.name, "member_count": len(team.members_json or [])},
    )


def get_team(db: Session, *, tenant_id: str, team_id: str) -> AgentTeam:
    team = db.scalar(
        select(AgentTeam).where(AgentTeam.id == team_id, AgentTeam.tenant_id == tenant_id)
    )
    if team is None:
        raise AppError("TEAM_NOT_FOUND", f"班组不存在：{team_id}。", http_status=404)
    return team


def list_teams(db: Session, *, tenant_id: str) -> list[AgentTeam]:
    return list(
        db.scalars(
            select(AgentTeam)
            .where(AgentTeam.tenant_id == tenant_id)
            .order_by(AgentTeam.created_at.desc(), AgentTeam.id.desc())
        )
    )


def create_team(
    db: Session,
    *,
    tenant_id: str,
    actor_id: str,
    name: Any,
    members: Any,
    model_hint: str | None = None,
    description: str | None = None,
    enabled: bool = True,
    request_id: str,
) -> AgentTeam:
    """创建班组：成员规整（2..8）+ 租户内 agent 校验 + 同名校验。"""
    clean = _clean_name(name)
    specs = parse_members(members)
    _validate_member_agents(db, tenant_id, specs)
    _require_name_free(db, tenant_id, clean)
    team = AgentTeam(
        tenant_id=tenant_id,
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
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="team_created",
        team=team,
        request_id=request_id,
    )
    return team


def update_team(
    db: Session,
    *,
    tenant_id: str,
    actor_id: str,
    team_id: str,
    fields: dict[str, Any],
    request_id: str,
) -> AgentTeam:
    """编辑班组：字段子集 name/description/model_hint/enabled/members。"""
    team = get_team(db, tenant_id=tenant_id, team_id=team_id)
    if not fields:
        raise AppError("INVALID_REQUEST", "没有需要更新的字段。", http_status=400)
    allowed = ("name", "description", "model_hint", "enabled", "members")
    for key in fields:
        if key not in allowed:
            raise AppError("INVALID_REQUEST", f"不支持更新的字段：{key}", http_status=400)
    if "name" in fields:
        clean = _clean_name(fields["name"])
        _require_name_free(db, tenant_id, clean, exclude_id=team.id)
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
        _validate_member_agents(db, tenant_id, specs)
        team.members_json = specs
    _audit_team(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="team_updated",
        team=team,
        request_id=request_id,
    )
    db.flush()
    return team


def delete_team(
    db: Session, *, tenant_id: str, actor_id: str, team_id: str, request_id: str
) -> AgentTeam:
    """删除班组定义；历史 team_runs 保留（软引用，不级联删审计资产）。"""
    team = get_team(db, tenant_id=tenant_id, team_id=team_id)
    _audit_team(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="team_deleted",
        team=team,
        request_id=request_id,
    )
    db.delete(team)
    db.flush()
    return team


# ---------------------------------------------------------------------------
# 3) 序列化
# ---------------------------------------------------------------------------


def _iso(dt: Any) -> str | None:
    return dt.isoformat() if dt is not None else None


def team_out(team: AgentTeam) -> dict[str, Any]:
    return {
        "id": team.id,
        "tenant_id": team.tenant_id,
        "name": team.name,
        "model_hint": team.model_hint,
        "description": team.description,
        "members": [dict(m) for m in (team.members_json or []) if isinstance(m, dict)],
        "enabled": bool(team.enabled),
        "created_by": team.created_by,
        "created_at": _iso(team.created_at),
        "updated_at": _iso(team.updated_at),
    }


def member_out(row: TeamRunMember) -> dict[str, Any]:
    return {
        "index": int(row.index),
        "role_name": row.role_name,
        "member_agent_id": row.member_agent_id,
        # 该成员声明式 run 的 run_id（前端可二跳内部步骤流）
        "task_id": row.task_id,
        "status": row.status,
        "input_preview": row.input_preview,
        "output": row.output,
        "error_text": row.error_text,
        "updated_at": _iso(row.updated_at),
    }


def run_out(run: TeamRun, members: list[TeamRunMember] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": run.id,
        "team_id": run.team_id,
        "tenant_id": run.tenant_id,
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
    }
    if members is not None:
        data["members"] = [member_out(m) for m in members]
    return data


def run_summary(run: TeamRun) -> dict[str, Any]:
    """列表摘要：不回带全文 answer/input（列表页不放大字段）。"""
    return {
        "id": run.id,
        "team_id": run.team_id,
        "status": run.status,
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


def get_team_run(db: Session, *, tenant_id: str, run_id: str) -> TeamRun:
    run = db.scalar(select(TeamRun).where(TeamRun.id == run_id, TeamRun.tenant_id == tenant_id))
    if run is None:
        raise AppError("TEAM_RUN_NOT_FOUND", f"班组运行不存在：{run_id}。", http_status=404)
    return run


def list_team_runs(db: Session, *, tenant_id: str, team_id: str) -> list[TeamRun]:
    return list(
        db.scalars(
            select(TeamRun)
            .where(TeamRun.tenant_id == tenant_id, TeamRun.team_id == team_id)
            .order_by(TeamRun.created_at.desc(), TeamRun.id.desc())
        )
    )


def members_of_run(db: Session, run: TeamRun) -> list[TeamRunMember]:
    return list(
        db.scalars(
            select(TeamRunMember)
            .where(TeamRunMember.team_run_id == run.id)
            .order_by(TeamRunMember.index.asc())
        )
    )


def require_members_runnable(db: Session, *, tenant_id: str, members: list[dict[str, Any]]) -> None:
    """运行前预检：全部成员存在、属于本租户且已发布（缺失/未发布 → 快速失败）。"""
    for m in members:
        agent = declarative.get_agent(
            db, tenant_id=tenant_id, agent_id=str(m.get("agent_id") or "")
        )
        if agent.status != STATUS_PUBLISHED:
            raise AppError(
                "TEAM_MEMBER_NOT_PUBLISHED",
                f"班组成员「{m.get('role_name')}」（{agent.agent_code}）当前状态为 "
                f"{agent.status}，需先发布为 published 才能参与接力。",
                http_status=400,
            )


def create_team_run(
    db: Session, *, team: AgentTeam, user_input: str, ctx: TenantContext
) -> TeamRun:
    """建运行台账（run + 全员 pending 成员行）并提交——执行前的 durable 起点。"""
    specs = parse_members(team.members_json)
    run = TeamRun(
        team_id=team.id,
        tenant_id=ctx.tenant_id,
        input=user_input.strip(),
        status=RUN_RUNNING,
        members_json=copy.deepcopy(specs),
        current_member=0,
        started_at=_utcnow(),
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
    db.commit()
    return run


def _harvest_completed_answer(steps: list[rs.AgentRunStep]) -> str | None:
    """checkpoint 已有 final ok 步 → 直接摘取答案（成员状态没落库的崩溃窗口）。"""
    for step in steps:
        if step.step_type == rs.STEP_FINAL and step.status == rs.STEP_OK:
            return str((step.output_json or {}).get("answer") or "")
    return None


async def _run_member(
    db: Session,
    *,
    ctx: TenantContext,
    run: TeamRun,
    row: TeamRunMember,
    spec: dict[str, Any],
    prior: list[tuple[str, str]],
) -> str:
    """执行单个成员的接力轮（预置 task_id → 声明式 run → 返回其 answer）。"""
    agent = declarative.get_agent(db, tenant_id=ctx.tenant_id, agent_id=row.member_agent_id)
    if agent.status != STATUS_PUBLISHED:
        raise AppError(
            "TEAM_MEMBER_NOT_PUBLISHED",
            f"成员「{row.role_name}」（{agent.agent_code}）当前状态为 {agent.status}，需先发布。",
            http_status=409,
        )
    question = build_member_question(
        run.input, prior, str(spec.get("instruction") or ""), bool(spec.get("is_critic"))
    )
    if not row.task_id:
        row.task_id = f"team-{uuid.uuid4().hex[:16]}"
    row.status = MEMBER_RUNNING
    row.input_preview = question[:INPUT_PREVIEW_CAP]
    row.error_text = None
    run.current_member = int(row.index)
    # 预置 task_id + running 先落库：这是崩溃后的成员级续跑点
    db.commit()

    task_id = str(row.task_id)
    steps = rs.list_steps(db, tenant_id=ctx.tenant_id, run_id=task_id)
    harvested = _harvest_completed_answer(steps)
    if steps and harvested is not None:
        # 上次执行已到 final（崩在成员状态写入之前）：直接收割，一次 LLM 都不跑
        return harvested
    resume = bool(steps)
    result = await declarative.run_declarative_agent(
        db,
        agent=agent,
        input_data={} if resume else {"question": question},
        ctx=ctx,
        task_id=task_id,
        resume_run_id=task_id if resume else None,
    )
    return str(result.output.get("answer") or "")


def _mark_member_failed(db: Session, run: TeamRun, row: TeamRunMember, detail: str) -> None:
    """成员失败护栏：整队停在断点（failed + last_error），账本先行、错误后抛。"""
    row.status = MEMBER_ERROR
    row.error_text = detail[:2000]
    run.status = RUN_FAILED
    run.last_error = detail[:2000]
    run.finished_at = _utcnow()
    db.commit()
    # W4 事件推送：班组名+断点成员（账本已 commit，推送失败不回滚不拖累重试）
    from app.modules.push import service as push_service

    team = db.get(AgentTeam, run.team_id)
    team_name = team.name if team is not None else run.team_id
    push_service.dispatch(
        db,
        tenant_id=run.tenant_id,
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


async def continue_team_run(db: Session, *, run: TeamRun, ctx: TenantContext) -> TeamRun:
    """从首个非 ok 成员起顺序接力到队尾（首次运行与 retry 共用同一条路径）。

    - 已 ok 成员绝不重跑：其 output 直接作为后序成员的接力上下文；
    - 每成员边界 commit（步边界释放写事务）；
    - 任一成员失败 → failed 停在断点，抛带 team_run_id 的原始错误。
    """
    specs = [dict(s) for s in (run.members_json or [])]
    run.status = RUN_RUNNING
    run.finished_at = None
    run.last_error = None
    db.commit()

    rows = members_of_run(db, run)
    for row in rows:
        if row.status == MEMBER_OK:
            continue
        idx = int(row.index)
        if idx >= len(specs):
            raise AppError(
                "TEAM_RUN_CHECKPOINT_INVALID",
                "班组运行快照与成员台账不一致，无法续跑。",
                http_status=409,
            )
        spec = specs[idx]
        prior = [
            (r.role_name, r.output or "")
            for r in rows
            if int(r.index) < idx and r.status == MEMBER_OK
        ]
        try:
            answer = await _run_member(db, ctx=ctx, run=run, row=row, spec=spec, prior=prior)
        except AppError as e:
            _mark_member_failed(db, run, row, f"{e.code}: {e.message}")
            raise _wrap_member_error(run, row, e) from e
        except Exception as e:  # 非业务异常同样停断点，不伪报
            detail = f"{type(e).__name__}: {e}"
            _mark_member_failed(db, run, row, detail)
            raise AppError(
                "TEAM_RUN_MEMBER_FAILED",
                f"班组成员「{row.role_name}」执行失败：{detail}",
                http_status=502,
                details={"team_run_id": run.id, "member_index": idx},
            ) from e
        row.output = answer
        row.status = MEMBER_OK
        row.error_text = None
        run.current_member = idx + 1
        # 成员边界即提交：接力上下文与断点位置比答案更 durable
        db.commit()

    parts = [
        (r.role_name, bool(specs[int(r.index)].get("is_critic")), r.output or "") for r in rows
    ]
    run.answer = compose_answer(parts)
    run.status = RUN_COMPLETED
    run.finished_at = _utcnow()
    db.commit()
    return run
