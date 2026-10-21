"""班组 REST API（挂载于 agents 路由之下，最终路径 /agents/teams* 与 /agents/team-runs*）。

选型说明：班组与声明式 Agent/工厂同属 agents 域（成员就是 tenant_agents 行），
故不新开顶层 /teams，而是挂在 /agents 下——鉴权依赖、错误码风格、审计口径
与 declarative/factory 子路由完全同构，api/v1 总路由无需新增感知。

- CRUD（tenant_admin）：POST/GET/PATCH/DELETE /agents/teams*
- 运行（租户成员均可）：POST /agents/teams/{id}/run；GET /agents/teams/{id}/runs
- 台账/续跑/推流（租户成员均可）：GET /agents/team-runs/{run_id}、
  POST /agents/team-runs/{run_id}/retry、GET /agents/team-runs/{run_id}/stream

运行是同步请求内执行（与声明式同步运行同一口径：只写审计 + llm_calls，
不建 tasks 行）；实时观看用成员级 SSE（stream 端点），历史用 GET 详情。
retry 语义：已 ok 成员绝不重跑；从首个非 ok 成员续跑，有 checkpoint 步骤的
成员走 W1 resume（副作用不重放），否则整段重跑。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.core.deps import (
    CurrentUser,
    TenantContext,
    get_current_user,
    get_db_session,
    get_tenant_context,
    require_roles,
)
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.agents.teams import service
from app.modules.agents.teams import stream as team_stream
from app.modules.agents.teams.models import RUN_COMPLETED, RUN_RUNNING
from app.modules.audit.service import log_audit

router = APIRouter(prefix="/teams", tags=["agent-teams"])
runs_router = APIRouter(prefix="/team-runs", tags=["agent-teams"])

#: 管理闸门：tenant_admin（require_roles 内置 platform_admin 直通）
_team_admin_gate = require_roles("tenant_admin")


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

    @field_validator("input")
    @classmethod
    def _input_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("input 不能为空白")
        return v.strip()


def _audit_run(
    db: Session,
    ctx: TenantContext,
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
        tenant_id=ctx.tenant_id,
        actor_id=actor_id,
        action=action,
        resource_type="agent_team",
        resource_id=str(run.team_id),
        result=result,
        request_id=ctx.request_id,
        metadata={"team_run_id": run.id, **metadata},
    )


# ---------------------------------------------------------------------------
# 班组 CRUD（tenant_admin 管理；读取放开给租户成员——班组成员可跑即可见）
# ---------------------------------------------------------------------------


@router.post("")
def create_team(
    payload: TeamCreateRequest,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    user: CurrentUser = Depends(_team_admin_gate),
    db: Session = Depends(get_db_session),
):
    """创建班组（成员 2..8、同租户重名 409、跨租户成员 404）。"""
    try:
        team = service.create_team(
            db,
            tenant_id=ctx.tenant_id,
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
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
):
    """本租户班组列表（租户成员均可读）。"""
    teams = service.list_teams(db, tenant_id=ctx.tenant_id)
    return ok({"teams": [service.team_out(t) for t in teams]}, request)


@router.get("/{team_id}")
def get_team(
    team_id: str,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
):
    """班组详情（含成员定义与成员 Agent 摘要）。"""
    team = service.get_team(db, tenant_id=ctx.tenant_id, team_id=team_id)
    return ok(service.team_out(team), request)


@router.patch("/{team_id}")
def update_team(
    team_id: str,
    payload: TeamUpdateRequest,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    user: CurrentUser = Depends(_team_admin_gate),
    db: Session = Depends(get_db_session),
):
    """编辑班组（字段子集；members 传则整单重校验）。"""
    fields = payload.model_dump(exclude_unset=True)
    try:
        team = service.update_team(
            db,
            tenant_id=ctx.tenant_id,
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
    ctx: TenantContext = Depends(get_tenant_context),
    user: CurrentUser = Depends(_team_admin_gate),
    db: Session = Depends(get_db_session),
):
    """删除班组定义（历史运行台账保留）。"""
    try:
        team = service.delete_team(
            db,
            tenant_id=ctx.tenant_id,
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


@router.post("/{team_id}/run")
async def run_team(
    team_id: str,
    payload: TeamRunRequest,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """同步跑一次班组接力：全员预检 → 按序执行 → 返回 run（含成员明细）。

    任一成员失败：HTTP 抛原始错误（run 已置 failed 停在断点，可 retry），
    与声明式运行失败 502 的口径一致。
    """
    team = service.get_team(db, tenant_id=ctx.tenant_id, team_id=team_id)
    if not team.enabled:
        raise AppError("TEAM_DISABLED", "班组已停用，不能发起运行。", http_status=409)
    specs = service.parse_members(team.members_json)
    service.require_members_runnable(db, tenant_id=ctx.tenant_id, members=specs)
    run = service.create_team_run(db, team=team, user_input=payload.input, ctx=ctx)
    try:
        run = await service.continue_team_run(db, run=run, ctx=ctx)
    except AppError as e:
        _audit_run(
            db,
            ctx,
            user.user_id,
            action="team_run",
            run=run,
            result="failure",
            metadata={"error_code": e.code, "failed_member": e.details.get("member_index")},
        )
        db.commit()
        raise
    _audit_run(
        db,
        ctx,
        user.user_id,
        action="team_run",
        run=run,
        result="success",
        metadata={
            "member_count": len(run.members_json or []),
            "answer_chars": len(run.answer or ""),
        },
    )
    db.commit()
    return ok(service.run_out(run, service.members_of_run(db, run)), request)


@router.get("/{team_id}/runs")
def list_team_runs(
    team_id: str,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
):
    """本班组运行列表（摘要，最新在前）。"""
    service.get_team(db, tenant_id=ctx.tenant_id, team_id=team_id)  # 边界：不存在 404
    runs = service.list_team_runs(db, tenant_id=ctx.tenant_id, team_id=team_id)
    return ok({"runs": [service.run_summary(r) for r in runs]}, request)


# ---------------------------------------------------------------------------
# 运行台账 / 断点续跑 / SSE（租户成员均可）
# ---------------------------------------------------------------------------


@runs_router.get("/{run_id}")
def get_team_run(
    run_id: str,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
):
    """运行详情：team_runs 主行 + 全部成员接力明细（含 task_id 供二跳）。"""
    run = service.get_team_run(db, tenant_id=ctx.tenant_id, run_id=run_id)
    return ok(service.run_out(run, service.members_of_run(db, run)), request)


@runs_router.post("/{run_id}/retry")
async def retry_team_run(
    run_id: str,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """成员级断点续跑：已 ok 成员绝不重跑（复用 output），从首个非 ok 成员续。

    有 checkpoint 步骤的成员以其存量 task_id 走 W1 resume（副作用工具不重放、
    失败 LLM 步原位重跑）；无步骤成员整段重跑（该路径无副作用工具）。
    审计 action=team_run_retry（记 skipped_ok 与续跑起点）。
    """
    run = service.get_team_run(db, tenant_id=ctx.tenant_id, run_id=run_id)
    if run.status == RUN_COMPLETED:
        raise AppError(
            "TEAM_RUN_ALREADY_COMPLETED", "该班组运行已完成，不允许重放。", http_status=409
        )
    if run.status == RUN_RUNNING:
        raise AppError(
            "TEAM_RUN_STATE_CONFLICT", "该班组运行仍在执行中，不能重复触发续跑。", http_status=409
        )
    team = service.get_team(db, tenant_id=ctx.tenant_id, team_id=run.team_id)
    if not team.enabled:
        raise AppError("TEAM_DISABLED", "班组已停用，不能续跑。", http_status=409)
    rows = service.members_of_run(db, run)
    base_meta = {
        "skipped_ok_members": sum(1 for r in rows if r.status == "ok"),
        "from_member_index": next((int(r.index) for r in rows if r.status != "ok"), len(rows)),
    }
    try:
        run = await service.continue_team_run(db, run=run, ctx=ctx)
    except AppError as e:
        _audit_run(
            db,
            ctx,
            user.user_id,
            action="team_run_retry",
            run=run,
            result="failure",
            metadata={**base_meta, "error_code": e.code},
        )
        db.commit()
        raise
    _audit_run(
        db,
        ctx,
        user.user_id,
        action="team_run_retry",
        run=run,
        result="success",
        metadata=dict(base_meta),
    )
    db.commit()
    return ok(service.run_out(run, service.members_of_run(db, run)), request)


@runs_router.get("/{run_id}/stream")
def stream_team_run(
    run_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
):
    """成员级 SSE（复用 run_stream 帧协议；内部步经 agent_run_id 二跳，见 stream.py）。"""
    return team_stream.build_stream_response(ctx.tenant_id, run_id)
