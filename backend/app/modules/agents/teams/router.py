"""班组管理、异步运行、执行者隔离的历史与文档接口。"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.core.deps import (
    CurrentUser,
    YardContext,
    get_db_session,
    get_yard_context,
    require_roles,
)
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.agents.teams import scheduler, service
from app.modules.agents.teams import stream as team_stream
from app.modules.agents.teams import worker_tasks as team_worker_tasks  # noqa: F401
from app.modules.agents.teams.models import RUN_COMPLETED
from app.modules.audit.service import log_audit

router = APIRouter(prefix="/teams", tags=["agent-teams"])
runs_router = APIRouter(prefix="/team-runs", tags=["agent-teams"])

#: 管理闸门：yard_admin（require_roles 内置 platform_admin 直通）
_team_admin_gate = require_roles("yard_admin")


class TeamCreateRequest(BaseModel):
    """创建班组：members 为有序数组（数量/字段校验在 service 层，报错更友好）。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    members: list[dict[str, Any]]
    model_hint: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=4000)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name 不能为空白")
        return v.strip()


class TeamUpdateRequest(BaseModel):
    """编辑班组：全部可选；members 传则整体替换（保持顺序语义简单）。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    members: list[dict[str, Any]] | None = None
    model_hint: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=4000)
    enabled: bool | None = None


class TeamRunRequest(BaseModel):
    """班组运行请求：input 为原始问题（各成员接力输入的公共前缀）。"""

    model_config = ConfigDict(extra="forbid")

    input: str = Field(min_length=1, max_length=8000)
    output_format: Literal["text", "md", "docx"] = "text"

    @field_validator("input")
    @classmethod
    def _input_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("input 不能为空白")
        return v.strip()


def _audit_run(
    db: Session,
    ctx: YardContext,
    actor_id: str,
    *,
    action: str,
    run: Any,
    result: str,
    metadata: dict[str, Any],
) -> None:
    """班组运行审计；永不影响主流程。"""
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=actor_id,
        action=action,
        resource_type="agent_team",
        resource_id=str(run.team_id),
        result=result,
        request_id=ctx.request_id,
        metadata={"team_run_id": run.id, **metadata},
    )


# ---------------------------------------------------------------------------
# 班组 CRUD（yard_admin 管理；读取放开给小院成员——班组成员可跑即可见）
# ---------------------------------------------------------------------------


@router.post("")
def create_team(
    payload: TeamCreateRequest,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_team_admin_gate),
    db: Session = Depends(get_db_session),
):
    """创建班组（成员 2..8、同小院重名 409、跨小院成员 404）。"""
    try:
        team = service.create_team(
            db,
            yard_id=ctx.yard_id,
            actor_id=user.user_id,
            name=payload.name,
            members=payload.members,
            model_hint=payload.model_hint,
            description=payload.description,
            enabled=payload.enabled,
            request_id=ctx.request_id,
        )
        db.commit()
    except AppError:
        db.rollback()
        raise
    return ok(service.team_out(team), request)


@router.get("")
def list_teams(
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    """本小院班组列表（小院成员均可读）。"""
    teams = service.list_teams(db, yard_id=ctx.yard_id)
    return ok({"teams": [service.team_out(t) for t in teams]}, request)


@router.get("/{team_id}")
def get_team(
    team_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    """班组详情（含成员定义与成员 Agent 摘要）。"""
    team = service.get_team(db, yard_id=ctx.yard_id, team_id=team_id)
    return ok(service.team_out(team), request)


@router.patch("/{team_id}")
def update_team(
    team_id: str,
    payload: TeamUpdateRequest,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_team_admin_gate),
    db: Session = Depends(get_db_session),
):
    """编辑班组（字段子集；members 传则整单重校验）。"""
    fields = payload.model_dump(exclude_unset=True)
    try:
        team = service.update_team(
            db,
            yard_id=ctx.yard_id,
            actor_id=user.user_id,
            team_id=team_id,
            fields=fields,
            request_id=ctx.request_id,
        )
        db.commit()
    except AppError:
        db.rollback()
        raise
    return ok(service.team_out(team), request)


@router.delete("/{team_id}")
def delete_team(
    team_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_team_admin_gate),
    db: Session = Depends(get_db_session),
):
    """删除班组定义（历史运行台账保留）。"""
    try:
        team = service.delete_team(
            db,
            yard_id=ctx.yard_id,
            actor_id=user.user_id,
            team_id=team_id,
            request_id=ctx.request_id,
        )
        db.commit()
    except AppError:
        db.rollback()
        raise
    return ok({"deleted": team.id}, request)


# ---------------------------------------------------------------------------
# 运行（成员可跑）
# ---------------------------------------------------------------------------


@router.put("/{team_id}/schedule")
def set_schedule(
    team_id: str,
    payload: scheduler.ScheduleSpec,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_team_admin_gate),
    db: Session = Depends(get_db_session),
):
    team = service.get_team(db, yard_id=ctx.yard_id, team_id=team_id)
    scheduler.set_schedule(db, team=team, ctx=ctx, schedule=payload)
    db.commit()
    return ok(service.team_out(team), request)


@router.post("/{team_id}/run", status_code=202)
def run_team(
    team_id: str,
    payload: TeamRunRequest,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    team = service.get_team(db, yard_id=ctx.yard_id, team_id=team_id)
    current = service.execution_context(
        db, yard_id=ctx.yard_id, user_id=ctx.user_id, request_id=ctx.request_id
    )
    try:
        run = service.create_team_run(
            db,
            team=team,
            user_input=payload.input,
            ctx=current,
            output_format=payload.output_format,
        )
        task = service.create_run_task(db, run=run, ctx=current)
        _audit_run(
            db,
            current,
            current.user_id,
            action="team_run",
            run=run,
            result="success",
            metadata={"status": "queued", "member_count": len(run.members_json)},
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    data = service.run_out(run, service.members_of_run(db, run))
    service.dispatch_run(db, run, task)
    return JSONResponse(status_code=202, content=ok(data, request))


@router.get("/{team_id}/runs")
def list_team_runs(
    team_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    service.get_team(db, yard_id=ctx.yard_id, team_id=team_id)
    runs = service.list_team_runs(db, yard_id=ctx.yard_id, team_id=team_id, user_id=ctx.user_id)
    return ok({"runs": [service.run_summary(r) for r in runs]}, request)


@runs_router.get("")
def list_runs(
    request: Request,
    team_id: str | None = None,
    status: Literal["queued", "running", "completed", "failed"] | None = None,
    trigger: Literal["manual", "schedule"] | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    return ok(
        service.paged_team_runs(
            db,
            ctx=ctx,
            team_id=team_id,
            status=status,
            trigger=trigger,
            page=page,
            page_size=page_size,
        ),
        request,
    )


@runs_router.get("/{run_id}")
def get_team_run(
    run_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    run = service.get_team_run(db, yard_id=ctx.yard_id, run_id=run_id, user_id=ctx.user_id)
    return ok(service.run_out(run, service.members_of_run(db, run)), request)


@runs_router.post("/{run_id}/retry", status_code=202)
def retry_team_run(
    run_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    run = service.get_team_run(db, yard_id=ctx.yard_id, run_id=run_id, user_id=ctx.user_id)
    try:
        task = service.prepare_retry(db, run=run, ctx=ctx)
        rows = service.members_of_run(db, run)
        _audit_run(
            db,
            ctx,
            ctx.user_id,
            action="team_run_retry",
            run=run,
            result="success",
            metadata={
                "skipped_ok_members": sum(r.status == "ok" for r in rows),
                "from_member_index": next((r.index for r in rows if r.status != "ok"), len(rows)),
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    data = service.run_out(run, rows)
    service.dispatch_run(db, run, task)
    return JSONResponse(status_code=202, content=ok(data, request))


@runs_router.get("/{run_id}/document")
def download_document(
    run_id: str,
    format: Literal["md", "docx"] = "md",
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    from app.modules.agents.teams.artifacts import render_document

    run = service.get_team_run(db, yard_id=ctx.yard_id, run_id=run_id, user_id=ctx.user_id)
    if run.status != RUN_COMPLETED:
        raise AppError(
            "TEAM_DOCUMENT_NOT_READY", "运行尚未成功完成，不能下载文档。", http_status=409
        )
    content, mime, filename = render_document(run, format)
    return Response(
        content=content,
        media_type=mime,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}"},
    )


@runs_router.get("/{run_id}/stream")
def stream_team_run(
    run_id: str,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    service.get_team_run(db, yard_id=ctx.yard_id, run_id=run_id, user_id=ctx.user_id)
    return team_stream.build_stream_response(ctx.yard_id, run_id, ctx.user_id)
