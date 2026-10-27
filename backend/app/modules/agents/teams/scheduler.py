"""间隔计划：CAS 领取与推进、运行及 Task 落库共用同一事务。"""

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.agents.teams import service
from app.modules.agents.teams.models import (
    MEMBER_PENDING,
    RUN_FAILED,
    RUN_QUEUED,
    RUN_RUNNING,
    AgentTeam,
    TeamRun,
    TeamRunMember,
)
from app.modules.audit.service import log_audit

log = get_logger(__name__)


class ScheduleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    interval_minutes: int = Field(default=1440, ge=5, le=43200)
    input: str = Field(default="", max_length=8000)
    output_format: Literal["text", "md", "docx"] = "text"

    @model_validator(mode="after")
    def require_input_when_enabled(self) -> "ScheduleSpec":
        self.input = self.input.strip()
        if self.enabled and not self.input:
            raise ValueError("启用定时任务时必须填写运行问题")
        return self


def set_schedule(db: Session, *, team: AgentTeam, ctx: YardContext, schedule: ScheduleSpec) -> None:
    current = service.execution_context(
        db, yard_id=ctx.yard_id, user_id=ctx.user_id, request_id=ctx.request_id
    )
    if not {"yard_admin", "yard_owner", "platform_admin"}.intersection(current.roles):
        raise AppError("FORBIDDEN", "需要小院管理员权限。", http_status=403)
    if schedule.enabled:
        service.require_members_runnable(
            db,
            yard_id=ctx.yard_id,
            members=service.parse_members(team.members_json),
            user_id=current.user_id,
        )
    team.schedule_json = schedule.model_dump()
    team.schedule_created_by = current.user_id
    team.next_run_at = (
        datetime.now(UTC) + timedelta(minutes=schedule.interval_minutes)
        if schedule.enabled
        else None
    )
    service._audit_team(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="team_schedule",
        team=team,
        request_id=ctx.request_id,
    )
    db.flush()


def _claim_due(db: Session, team: AgentTeam, now: datetime, interval: int) -> bool:
    result = db.execute(
        update(AgentTeam)
        .where(
            AgentTeam.id == team.id,
            AgentTeam.next_run_at == team.next_run_at,
            AgentTeam.next_run_at <= now,
            AgentTeam.enabled.is_(True),
            AgentTeam.schedule_json["enabled"].as_boolean().is_(True),
        )
        .values(next_run_at=now + timedelta(minutes=interval))
        .execution_options(synchronize_session=False)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


def _record_failure(db: Session, team: AgentTeam, now: datetime, detail: str) -> None:
    raw = team.schedule_json or {}
    run = TeamRun(
        team_id=team.id,
        team_name=team.name,
        yard_id=team.yard_id,
        created_by=team.schedule_created_by,
        trigger="schedule",
        output_format=raw.get("output_format")
        if raw.get("output_format") in {"md", "docx"}
        else "text",
        input=str(raw.get("input") or ""),
        status=RUN_FAILED,
        members_json=team.members_json or [],
        current_member=0,
        last_error=detail[:2000],
        finished_at=now,
    )
    db.add(run)
    db.flush()
    # 与手动运行同构地落成员台账，修复执行者后可直接重试该次失败。
    try:
        specs = service.parse_members(team.members_json)
    except AppError:
        specs = []
    for index, spec in enumerate(specs):
        db.add(
            TeamRunMember(
                team_run_id=run.id,
                index=index,
                member_agent_id=str(spec["agent_id"]),
                role_name=str(spec["role_name"]),
                status=MEMBER_PENDING,
            )
        )


def _reap_stale_runs(db: Session, now: datetime) -> int:
    """与通用任务回收同口径：进程崩溃/投递丢失时班组运行不能永久占住班组。"""
    queued_cutoff = now - timedelta(minutes=settings.task_queued_timeout_minutes)
    running_cutoff = now - timedelta(minutes=settings.task_running_timeout_minutes)
    stale = list(
        db.scalars(
            select(TeamRun).where(
                or_(
                    (TeamRun.status == RUN_QUEUED) & (TeamRun.created_at < queued_cutoff),
                    (TeamRun.status == RUN_RUNNING) & (TeamRun.updated_at < running_cutoff),
                )
            )
        )
    )
    for run in stale:
        run.status = RUN_FAILED
        run.last_error = "TEAM_RUN_TIMEOUT: 运行超时未完成，系统已回收，可重新发起。"
        run.finished_at = now
    if stale:
        db.commit()
        log.warning("team runs reaped: %d", len(stale))
    return len(stale)


def run_due_jobs(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(UTC)
    _reap_stale_runs(db, now)
    stats = {"due": 0, "queued": 0, "failed": 0, "skipped": 0}
    due_ids = list(
        db.scalars(
            select(AgentTeam.id)
            .where(
                AgentTeam.enabled.is_(True),
                AgentTeam.next_run_at <= now,
                AgentTeam.schedule_json["enabled"].as_boolean().is_(True),
            )
            .order_by(AgentTeam.next_run_at, AgentTeam.id)
            .limit(100)
        )
    )
    for team_id in due_ids:
        task = run = None
        try:
            team = db.get(AgentTeam, team_id, populate_existing=True)
            if team is None:
                continue
            try:
                spec = ScheduleSpec.model_validate(team.schedule_json)
            except ValidationError:
                spec = None
            if not _claim_due(db, team, now, spec.interval_minutes if spec else 1440):
                db.rollback()
                continue
            stats["due"] += 1
            db.refresh(team)
            try:
                if spec is None:
                    raise AppError("TEAM_SCHEDULE_INVALID", "计划配置无效。", http_status=400)
                ctx = service.execution_context(
                    db,
                    yard_id=team.yard_id,
                    user_id=team.schedule_created_by or "",
                    request_id=f"req_team_schedule_{team.id}",
                )
                run = service.create_team_run(
                    db,
                    team=team,
                    user_input=spec.input,
                    ctx=ctx,
                    output_format=spec.output_format,
                    trigger="schedule",
                )
                task = service.create_run_task(db, run=run, ctx=ctx)
                stats["queued"] += 1
            except AppError as exc:
                if exc.code == "TEAM_BUSY":
                    stats["skipped"] += 1
                else:
                    _record_failure(db, team, now, f"{exc.code}: {exc.message}")
                    stats["failed"] += 1
                log_audit(
                    db,
                    yard_id=team.yard_id,
                    actor_id=team.schedule_created_by,
                    action="team_schedule_skipped"
                    if exc.code == "TEAM_BUSY"
                    else "team_schedule_failed",
                    resource_type="agent_team",
                    resource_id=team.id,
                    result="failure",
                    request_id="req_team_schedule",
                    metadata={"reason": exc.code, "message": exc.message},
                )
            db.commit()
        except Exception:
            db.rollback()
            log.exception("team schedule tick failed: %s", team_id)
            continue
        if task is not None and run is not None:
            service.dispatch_run(db, run, task)
    return stats
