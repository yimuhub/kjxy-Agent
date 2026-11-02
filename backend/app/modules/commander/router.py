"""指挥官 REST（计划预览 / 计划编辑 / 运行详情 / 执行 / 重试 / 取消 / 指标）。

设置接口由 ``yards/settings_router.py`` 统一提供，这里不重复。
所有接口要求登录；读取/操作按 ``yard_id + 创建者或小院负责人`` 边界校验，
越界统一 404 ``COMMANDER_RUN_NOT_FOUND``（不泄露他人运行是否存在）。
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, YardContext, get_current_user, get_db_session
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.audit.service import log_audit
from app.modules.commander import (
    evaluator,
    learning,
    metrics,
    planner,
    reports,
    service,
    snapshots,
)
from app.modules.commander import settings as commander_settings
from app.modules.commander.models import RUN_QUEUED, CommanderRun
from app.modules.memory import service as memory_service

router = APIRouter(prefix="/commander", tags=["commander"])

#: 后台任务强引用集合（避免 create_task 返回值被 GC 提前回收）
_BACKGROUND_TASKS: set[asyncio.Task] = set()


class PreviewRequest(BaseModel):
    """只规划不执行：内容 + 会话 + 可选附件/历史上下文。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    content: str = Field(min_length=1, max_length=4000)
    session_id: str = Field(min_length=1, max_length=100)
    context: str = Field(default="", max_length=8000)


class PlanSubtaskIn(BaseModel):
    """计划卡提交的一个子任务；depends_on 按新数组位置 1-based 解释。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(min_length=1, max_length=200)
    instruction: str = Field(min_length=1, max_length=8000)
    expected_output: str | None = Field(default=None, max_length=1000)
    agent_id: str | None = Field(default=None, max_length=36)
    depends_on: list[int] | None = None


class PlanEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    subtasks: list[PlanSubtaskIn] = Field(min_length=1, max_length=12)


class EvaluationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    use_llm: bool = True
    force: bool = False


class SnapshotCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    label: str = Field(default="", max_length=60)
    reason: str = Field(default="", max_length=200)


class WorkflowStepIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(min_length=1, max_length=200)
    instruction: str = Field(min_length=1, max_length=2000)
    expected_output: str = Field(default="", max_length=1000)
    agent_code: str = Field(default="", max_length=100)


class WorkflowCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=200)
    scene: str = Field(min_length=1, max_length=4000)
    preconditions: str = Field(default="", max_length=2000)
    steps: list[WorkflowStepIn] = Field(min_length=1, max_length=20)


class WorkflowUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["active", "draft", "archived"] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    scene: str | None = Field(default=None, min_length=1, max_length=4000)
    preconditions: str | None = Field(default=None, max_length=2000)
    steps: list[WorkflowStepIn] | None = Field(default=None, min_length=1, max_length=20)


class FailureUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["watching", "active", "resolved"]


class SkillUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["candidate", "active", "archived"]


class IterationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")


def _is_manager(ctx: YardContext) -> bool:
    return bool(set(ctx.roles or []) & {"yard_owner", "yard_admin", "platform_admin"})


def _require_manager(ctx: YardContext, message: str = "仅小院负责人或管理员可执行该操作。") -> None:
    if not _is_manager(ctx):
        raise AppError("FORBIDDEN", message, http_status=403)


def _ctx(user: CurrentUser) -> YardContext:
    if user.yard_id is None:
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    return YardContext(
        yard_id=user.yard_id,
        user_id=user.user_id,
        roles=list(user.roles),
        request_id=user.request_id,
    )


def _load(db: Session, user: CurrentUser, run_id: str):
    run, subtasks = service.get_run(db, _ctx(user).yard_id, run_id)
    service.require_run_access(db, run, user_id=user.user_id, roles=user.roles)
    return run, subtasks


@router.get("/stats")
def commander_stats(
    request: Request,
    days: int = 30,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    ctx = _ctx(user)
    return ok({"stats": metrics.stats(db, ctx.yard_id, max(1, min(int(days), 365)))}, request)


@router.get("/cost")
def commander_cost(
    request: Request,
    days: int = 30,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """按用途/模型聚合成本；数据来自既有 LLM 调用账本，不新增记账链路。"""
    ctx = _ctx(user)
    return ok(metrics.cost_stats(db, ctx.yard_id, max(1, min(int(days), 365))), request)


@router.get("/overview")
def commander_overview(
    request: Request,
    days: int = 30,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """指挥台首页：设置快照、运行指标、成本、记忆计数与最近运行。"""
    ctx = _ctx(user)
    window_days = max(1, min(int(days), 365))
    settings = commander_settings.settings_for(db, ctx.yard_id)
    rows = list(
        db.scalars(
            select(CommanderRun)
            .where(CommanderRun.yard_id == ctx.yard_id)
            .order_by(CommanderRun.created_at.desc())
            .limit(8)
        )
    )
    runs: list[dict] = []
    for run in rows:
        _run, subtasks = service.get_run(db, ctx.yard_id, run.id)
        runs.append(service.run_out(run, subtasks))
    summaries = memory_service.list_memories(db, ctx, kind="summary", limit=1)
    manager = _is_manager(ctx)
    latest_eval = evaluator.latest_evaluation(db, ctx.yard_id) if manager else None
    snapshot_rows = snapshots.list_snapshots(db, ctx.yard_id, limit=5) if manager else []
    return ok(
        {
            "commander": {
                "display_name": settings["display_name"],
                "emoji": settings["emoji"],
                "enabled": bool(settings["enabled"]),
                "token_budget": int(settings["token_budget"]),
                "max_parallel": int(settings["max_parallel"]),
                "default_mode": bool(settings["default_mode"]),
                "clarify_enabled": bool(settings["clarify_enabled"]),
                "auto_remediate": bool(settings["auto_remediate"]),
                "persona": settings["persona"],
                "persona_locked": bool(settings["persona_locked"]),
                "evolution_lock": bool(settings["evolution_lock"]),
                "evaluator_enabled": bool(settings["evaluator_enabled"]),
            },
            "stats": metrics.stats(db, ctx.yard_id, window_days),
            "cost": metrics.cost_stats(db, ctx.yard_id, window_days),
            "memory": {
                "counts": memory_service.count_memories(db, ctx),
                "latest_summary": (memory_service.memory_out(summaries[0]) if summaries else None),
            },
            "governance": {
                "latest_evaluation": (
                    evaluator.evaluation_out(latest_eval) if latest_eval is not None else None
                ),
                "snapshots": [snapshots.snapshot_out(row) for row in snapshot_rows],
                "assets": learning.asset_counts(db, ctx.yard_id) if manager else {},
            },
            "runs": runs,
        },
        request,
    )


@router.get("/evaluations")
def list_evaluations(
    request: Request,
    limit: int = 12,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """历史测评报告；只对负责人/管理员开放。"""
    ctx = _ctx(user)
    _require_manager(ctx, "仅小院负责人或管理员可查看测评报告。")
    rows = evaluator.list_evaluations(db, ctx.yard_id, limit=limit)
    return ok({"items": [evaluator.evaluation_out(row) for row in rows]}, request)


@router.get("/evaluations/latest")
def latest_evaluation(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    ctx = _ctx(user)
    _require_manager(ctx, "仅小院负责人或管理员可查看测评报告。")
    row = evaluator.latest_evaluation(db, ctx.yard_id)
    return ok({"evaluation": evaluator.evaluation_out(row) if row is not None else None}, request)


@router.post("/evaluations/run")
async def run_evaluation(
    body: EvaluationRunRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """手动触发本周测评；``use_llm=false`` 时只跑确定性规则评分。"""
    ctx = _ctx(user)
    _require_manager(ctx)
    row, created = await evaluator.run_evaluation(db, ctx, use_llm=body.use_llm, force=body.force)
    db.commit()
    return ok(
        {
            "evaluation": evaluator.evaluation_out(row),
            "created": created,
            "settings": commander_settings.settings_for(db, ctx.yard_id),
        },
        request,
    )


@router.get("/snapshots")
def list_snapshots(
    request: Request,
    limit: int = 20,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    ctx = _ctx(user)
    _require_manager(ctx, "仅小院负责人或管理员可查看快照。")
    rows = snapshots.list_snapshots(db, ctx.yard_id, limit=limit)
    return ok({"items": [snapshots.snapshot_out(row) for row in rows]}, request)


@router.post("/snapshots")
def create_snapshot(
    body: SnapshotCreateRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    ctx = _ctx(user)
    _require_manager(ctx, "仅小院负责人或管理员可创建快照。")
    row = snapshots.create_snapshot(
        db,
        yard_id=ctx.yard_id,
        label=body.label,
        reason=body.reason,
        created_by=ctx.user_id,
    )
    db.commit()
    return ok({"snapshot": snapshots.snapshot_out(row)}, request)


@router.post("/snapshots/{snapshot_id}/restore")
def restore_snapshot(
    snapshot_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    ctx = _ctx(user)
    _require_manager(ctx, "仅小院负责人或管理员可回滚快照。")
    backup, settings = snapshots.restore_snapshot(
        db,
        yard_id=ctx.yard_id,
        snapshot_id=snapshot_id,
        actor_id=ctx.user_id,
        request_id=ctx.request_id,
    )
    db.commit()
    return ok(
        {
            "backup": snapshots.snapshot_out(backup),
            "settings": settings,
        },
        request,
    )


def _assets_payload(db: Session, yard_id: str) -> dict:
    return {
        "counts": learning.asset_counts(db, yard_id),
        "workflows": [learning.workflow_out(row) for row in learning.list_workflows(db, yard_id)],
        "failures": [learning.failure_out(row) for row in learning.list_failures(db, yard_id)],
        "skills": [learning.skill_out(row) for row in learning.list_skills(db, yard_id)],
    }


@router.get("/assets")
def asset_overview(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """经验资产总览：工作流、失败黑名单、技能片段与计数。"""
    ctx = _ctx(user)
    _require_manager(ctx, "仅小院负责人或管理员可查看经验资产。")
    return ok(_assets_payload(db, ctx.yard_id), request)


@router.post("/assets/scan")
def scan_assets(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """手动回填近 30 天运行并重算技能；受小院学习开关约束。"""
    ctx = _ctx(user)
    _require_manager(ctx, "仅小院负责人或管理员可触发经验沉淀。")
    cfg = commander_settings.settings_for(db, ctx.yard_id)
    result = learning.scan_yard(
        db,
        yard_id=ctx.yard_id,
        failure_threshold=int(cfg.get("failure_blacklist_threshold") or 2),
        learn_workflows=bool(cfg.get("auto_learn_workflows", True)),
        record_failures=bool(cfg.get("auto_learn_workflows", True)),
        extract_skills=bool(cfg.get("auto_extract_skills", True)),
    )
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="commander_assets_scanned",
        resource_type="commander_asset",
        resource_id=None,
        result="success",
        request_id=ctx.request_id,
        metadata=result,
    )
    db.commit()
    return ok({**result, **_assets_payload(db, ctx.yard_id)}, request)


@router.post("/assets/workflows")
def create_workflow(
    body: WorkflowCreateRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    ctx = _ctx(user)
    _require_manager(ctx, "仅小院负责人或管理员可创建工作流。")
    row = learning.create_workflow(
        db,
        yard_id=ctx.yard_id,
        name=body.name,
        scene=body.scene,
        preconditions=body.preconditions,
        steps=[step.model_dump() for step in body.steps],
        created_by=ctx.user_id,
    )
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="commander_workflow_created",
        resource_type="commander_workflow",
        resource_id=row.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"code": row.code, "steps": len(row.steps_json or [])},
    )
    db.commit()
    return ok({"workflow": learning.workflow_out(row)}, request)


@router.put("/assets/workflows/{workflow_id}")
def update_workflow(
    workflow_id: str,
    body: WorkflowUpdateRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    ctx = _ctx(user)
    _require_manager(ctx, "仅小院负责人或管理员可修改工作流。")
    row = learning.get_workflow(db, ctx.yard_id, workflow_id)
    if row is None:
        raise AppError("COMMANDER_ASSET_NOT_FOUND", "工作流不存在。", http_status=404)
    learning.update_workflow(
        row,
        status=body.status,
        name=body.name,
        scene=body.scene,
        preconditions=body.preconditions,
        steps=[step.model_dump() for step in body.steps] if body.steps is not None else None,
    )
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="commander_workflow_updated",
        resource_type="commander_workflow",
        resource_id=row.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"status": row.status, "version": row.version},
    )
    db.commit()
    return ok({"workflow": learning.workflow_out(row)}, request)


@router.put("/assets/failures/{failure_id}")
def update_failure(
    failure_id: str,
    body: FailureUpdateRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    ctx = _ctx(user)
    _require_manager(ctx, "仅小院负责人或管理员可处理失败黑名单。")
    row = learning.get_failure(db, ctx.yard_id, failure_id)
    if row is None:
        raise AppError("COMMANDER_ASSET_NOT_FOUND", "失败案例不存在。", http_status=404)
    learning.update_failure_status(row, status=body.status)
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="commander_failure_updated",
        resource_type="commander_failure",
        resource_id=row.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"status": row.status},
    )
    db.commit()
    return ok({"failure": learning.failure_out(row)}, request)


@router.put("/assets/skills/{skill_id}")
def update_skill(
    skill_id: str,
    body: SkillUpdateRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    ctx = _ctx(user)
    _require_manager(ctx, "仅小院负责人或管理员可管理技能片段。")
    row = learning.get_skill(db, ctx.yard_id, skill_id)
    if row is None:
        raise AppError("COMMANDER_ASSET_NOT_FOUND", "技能片段不存在。", http_status=404)
    learning.update_skill_status(row, status=body.status)
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="commander_skill_updated",
        resource_type="commander_skill",
        resource_id=row.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"status": row.status},
    )
    db.commit()
    return ok({"skill": learning.skill_out(row)}, request)


@router.get("/prompt-preview")
def prompt_preview(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """当前人格与模块开关下的规划系统提示词预览（只读，不调用模型）。"""
    ctx = _ctx(user)
    _require_manager(ctx, "仅小院负责人或管理员可查看提示词预览。")
    cfg = commander_settings.settings_for(db, ctx.yard_id)
    return ok(planner.preview_system_prompt(cfg), request)


@router.get("/reports/daily")
def daily_report(
    request: Request,
    day: str | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """某日复盘报告；默认今天，确定性生成。"""
    ctx = _ctx(user)
    _require_manager(ctx, "仅小院负责人或管理员可查看日复盘。")
    try:
        target = date.fromisoformat(day) if day else date.today()
    except ValueError as exc:
        raise AppError("INVALID_REQUEST", "日期格式应为 YYYY-MM-DD。", http_status=400) from exc
    return ok(reports.daily_report(db, yard_id=ctx.yard_id, day=target), request)


@router.get("/reports/weekly")
def weekly_report(
    request: Request,
    week: str | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """周优化报告：测评结论 + 资产增量 + 成本对比 + 下周建议。"""
    ctx = _ctx(user)
    _require_manager(ctx, "仅小院负责人或管理员可查看周优化报告。")
    if week:
        try:
            year_text, week_text = week.split("-W", 1)
            date.fromisocalendar(int(year_text), int(week_text), 1)
        except (ValueError, TypeError) as exc:
            raise AppError(
                "INVALID_REQUEST", "周格式应为 YYYY-Www（如 2026-W45）。", http_status=400
            ) from exc
    return ok(reports.weekly_optimization(db, yard_id=ctx.yard_id, week_key=week), request)


@router.get("/iterations")
def list_iterations(
    request: Request,
    limit: int = 24,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    ctx = _ctx(user)
    _require_manager(ctx, "仅小院负责人或管理员可查看月度版本。")
    rows = reports.list_iterations(db, ctx.yard_id, limit=limit)
    return ok({"items": [reports.iteration_out(row) for row in rows]}, request)


@router.get("/iterations/latest")
def latest_iteration(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    ctx = _ctx(user)
    _require_manager(ctx, "仅小院负责人或管理员可查看月度版本。")
    row = reports.latest_iteration(db, ctx.yard_id)
    return ok({"iteration": reports.iteration_out(row) if row else None}, request)


@router.post("/iterations/run")
def run_iteration(
    body: IterationRunRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """生成月度迭代版本（默认上一个完整月）；同月幂等。"""
    ctx = _ctx(user)
    _require_manager(ctx, "仅小院负责人或管理员可执行月度迭代。")
    row, created = reports.run_iteration(db, ctx=ctx, month=body.month)
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="commander_iteration_created" if created else "commander_iteration_viewed",
        resource_type="commander_iteration",
        resource_id=row.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"month": row.month_key, "version": row.version_label},
    )
    db.commit()
    return ok(
        {
            "iteration": reports.iteration_out(row),
            "created": created,
        },
        request,
    )


@router.post("/preview")
async def preview_plan(
    body: PreviewRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """只规划不执行：落 queued 运行与子任务，供前端计划卡预览/编辑。"""
    ctx = _ctx(user)
    run = await service.create_plan_run(
        db,
        ctx=ctx,
        session_id=body.session_id,
        user_content=body.content,
        context=body.context,
    )
    _run, subtasks = service.get_run(db, ctx.yard_id, run.id)
    return ok(
        {
            "run": service.run_out(run, subtasks),
            "clarify": list((run.plan_json or {}).get("clarify") or []),
        },
        request,
    )


@router.put("/runs/{run_id}/plan")
def edit_plan(
    run_id: str,
    body: PlanEditRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    ctx = _ctx(user)
    run = service.update_plan(db, ctx, run_id, [item.model_dump() for item in body.subtasks])
    _run, subtasks = service.get_run(db, ctx.yard_id, run.id)
    return ok({"run": service.run_out(run, subtasks)}, request)


@router.get("/runs/{run_id}")
def get_run_detail(
    run_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    run, subtasks = _load(db, user, run_id)
    return ok({"run": service.run_out(run, subtasks)}, request)


@router.post("/runs/{run_id}/execute", status_code=202)
async def execute_run(
    run_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    run, _subtasks = _load(db, user, run_id)
    if run.status != RUN_QUEUED:
        raise AppError(
            "COMMANDER_RUN_STATE",
            f"当前状态为 {run.status}，只有排队中的运行可以启动。",
            http_status=409,
        )
    task = asyncio.create_task(service.run_in_background(run_id))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return ok({"run_id": run.id, "status": run.status}, request)


@router.post("/runs/{run_id}/retry")
def retry_run(
    run_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    ctx = _ctx(user)
    run = service.retry_run(db, ctx, run_id)
    _run, subtasks = service.get_run(db, ctx.yard_id, run.id)
    return ok({"run": service.run_out(run, subtasks)}, request)


@router.post("/runs/{run_id}/cancel")
def cancel_run(
    run_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    ctx = _ctx(user)
    run = service.cancel_run(db, ctx, run_id)
    _run, subtasks = service.get_run(db, ctx.yard_id, run.id)
    return ok({"run": service.run_out(run, subtasks)}, request)
