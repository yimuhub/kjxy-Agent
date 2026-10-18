"""Agent 工厂 REST API（/agents/factory*，挂载于 agents 路由之下）。

- POST /agents/factory/generate：建 FactoryRun + agent_factory 任务并立即返回
  {task_id, run_id}（tenant_admin；tasks 配额检查 + 审计 action=factory_generate）。
- GET  /agents/factory/runs：本租户运行列表（含蓝图摘要，不含 blueprint_json）。
- GET  /agents/factory/runs/{run_id}：详情（含完整 blueprint_json）。
- POST /agents/factory/runs/{run_id}/apply：人工采纳——仅 run 创建者或
  tenant_admin；publish=true 逐项走 declarative 分级发布（低风险自批 published，
  高风险进 pending_platform_review），publish=false 保持 draft；审计
  action=factory_apply。

进度实时性：任务的里程碑事件写入 TaskEvent（见 factory_pipeline），工厂页用
GET /tasks/{task_id}/events/stream 的 SSE 帧（event: task-event）实时追踪。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
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
from app.modules.agents import declarative
from app.modules.agents import factory_pipeline as pipeline  # noqa: F401 - 注册任务 handler
from app.modules.agents.factory_models import STATUS_COMPLETED, FactoryRun
from app.modules.audit.service import log_audit
from app.modules.tasks.router import _enqueue_or_inline
from app.modules.tasks.service import TaskService
from app.modules.tenants.quota import check_and_consume

router = APIRouter(prefix="/factory", tags=["agents-factory"])

#: 工厂操作闸门：tenant_admin（require_roles 内置 platform_admin 直通）
_factory_gate = require_roles("tenant_admin")

_BRIEF_PREVIEW_CHARS = 40


class FactoryGenerateRequest(BaseModel):
    """生成请求：industry_brief 必填非空白；focus 可选侧重方向。"""

    model_config = ConfigDict(extra="forbid")

    industry_brief: str = Field(min_length=1, max_length=2000)
    focus: str | None = Field(default=None, max_length=200)

    @field_validator("industry_brief")
    @classmethod
    def _brief_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("industry_brief 不能为空白")
        return v.strip()

    @field_validator("focus")
    @classmethod
    def _strip_focus(cls, v: str | None) -> str | None:
        return v.strip() if v and v.strip() else None


class FactoryApplyRequest(BaseModel):
    """采纳请求：codes 为蓝图内 agent_code 子集；publish 决定是否触发分级发布。"""

    model_config = ConfigDict(extra="forbid")

    codes: list[str] = Field(min_length=1, max_length=20)
    publish: bool = True

    @field_validator("codes")
    @classmethod
    def _codes_clean(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for c in v:
            s = str(c).strip()
            if not s:
                raise ValueError("codes 不能包含空白项")
            if s not in out:
                out.append(s)
        return out


def _iso(dt: Any) -> str | None:
    return dt.isoformat() if dt is not None else None


def _run_summary(run: FactoryRun) -> dict[str, Any]:
    """列表用摘要：不内联完整蓝图，附角色/缺口计数。"""
    bp = run.blueprint_json if isinstance(run.blueprint_json, dict) else None
    agents = bp.get("agents") if bp else None
    agents = agents if isinstance(agents, list) else []
    return {
        "id": run.id,
        "status": run.status,
        "industry_brief": run.industry_brief,
        "generated_by": run.generated_by,
        "error": run.error,
        "created_by": run.created_by,
        "approved_by": run.approved_by,
        "created_at": _iso(run.created_at),
        "updated_at": _iso(run.updated_at),
        "role_count": len(agents) or None,
        "needs_knowledge_count": (
            sum(1 for a in agents if isinstance(a, dict) and a.get("needs_knowledge"))
            if agents
            else None
        ),
    }


def _get_run(db: Session, tenant_id: str, run_id: str) -> FactoryRun:
    """租户边界取运行；跨租户/不存在一律 404（防枚举）。"""
    run = db.scalar(
        select(FactoryRun).where(FactoryRun.id == run_id, FactoryRun.tenant_id == tenant_id)
    )
    if run is None:
        raise AppError("FACTORY_RUN_NOT_FOUND", "工厂运行不存在或无权访问。", http_status=404)
    return run


@router.post("/generate")
def generate(
    payload: FactoryGenerateRequest,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    user: CurrentUser = Depends(_factory_gate),
    db: Session = Depends(get_db_session),
):
    """发起一次工厂生成：建 run + agent_factory 任务，立即返回句柄（202）。"""
    # 配额：一次工厂生成 = 一次任务（超限 429 QUOTA_EXCEEDED）；
    # 蓝图阶段的 LLM 调用另按 llm_calls 计（complete_with_logging 内部扣减）。
    check_and_consume(db, ctx.tenant_id, "tasks")
    run = FactoryRun(
        tenant_id=ctx.tenant_id,
        industry_brief=payload.industry_brief,
        created_by=user.user_id,
    )
    db.add(run)
    db.flush()
    try:
        task = TaskService.create(
            db,
            ctx,
            task_type=pipeline.FACTORY_TASK_TYPE,
            input={
                "industry_brief": payload.industry_brief,
                "focus": payload.focus,
                "run_id": run.id,
            },
            request_id=ctx.request_id,
        )
        log_audit(
            db,
            tenant_id=ctx.tenant_id,
            actor_id=user.user_id,
            action="factory_generate",
            resource_type="factory_run",
            resource_id=run.id,
            result="success",
            request_id=ctx.request_id,
            metadata={
                "task_id": task.id,
                "brief_preview": payload.industry_brief[:_BRIEF_PREVIEW_CHARS],
            },
        )
        db.commit()
    except AppError:
        db.rollback()
        raise
    # Celery 投递；tasks_inline_mode/worker_sync_fallback 语义与 tasks 模块一致
    _enqueue_or_inline(db, task)
    return JSONResponse(
        status_code=202,
        content=ok({"task_id": task.id, "run_id": run.id}, request),
    )


@router.get("/runs")
def list_runs(
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
):
    """本租户工厂运行列表（最新在前；租户成员均可读，便于跟踪进度）。"""
    runs = list(
        db.scalars(
            select(FactoryRun)
            .where(FactoryRun.tenant_id == ctx.tenant_id)
            .order_by(FactoryRun.created_at.desc(), FactoryRun.id.desc())
        )
    )
    return ok({"runs": [_run_summary(r) for r in runs]}, request)


@router.get("/runs/{run_id}")
def get_run(
    run_id: str,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
):
    """运行详情：完整 blueprint_json（未生成时为 null）+ 任务句柄反查提示。"""
    run = _get_run(db, ctx.tenant_id, run_id)
    data = _run_summary(run)
    data["blueprint_json"] = run.blueprint_json
    return ok(data, request)


@router.post("/runs/{run_id}/apply")
def apply_run(
    run_id: str,
    payload: FactoryApplyRequest,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """人工采纳蓝图：逐项结果 + 汇总；publish=true 走 declarative 分级发布。

    权限：仅 run 创建者或 tenant_admin（platform_admin 直通）；跨租户 404。
    """
    run = _get_run(db, ctx.tenant_id, run_id)
    is_admin = bool({"tenant_admin", "platform_admin"} & set(user.roles))
    if run.created_by != user.user_id and not is_admin:
        raise AppError(
            "FORBIDDEN", "只有该工厂运行的创建者或租户管理员可以采纳蓝图。", http_status=403
        )
    if run.status != STATUS_COMPLETED:
        raise AppError(
            "FACTORY_RUN_NOT_READY",
            f"运行当前状态（{run.status}）不可采纳，需等待 completed。",
            http_status=409,
        )
    bp = run.blueprint_json if isinstance(run.blueprint_json, dict) else None
    agents_raw = bp.get("agents") if bp else None
    items = [a for a in (agents_raw or []) if isinstance(a, dict)]
    by_code = {str(a.get("agent_code")): a for a in items}

    results: list[dict[str, Any]] = []
    summary = {
        "requested": len(payload.codes),
        "applied": 0,
        "failed": 0,
        "published": 0,
        "pending_review": 0,
        "draft": 0,
    }
    for code in payload.codes:
        entry: dict[str, Any] = {"agent_code": code, "agent_id": None, "ok": False, "status": None}
        item = by_code.get(code)
        if item is None:
            entry["error"] = "code_not_in_blueprint"
            results.append(entry)
            summary["failed"] += 1
            continue
        entry["agent_id"] = item.get("agent_id")
        try:
            agent = declarative.get_agent(
                db, tenant_id=ctx.tenant_id, agent_id=str(item.get("agent_id") or "")
            )
            if payload.publish:
                agent = declarative.publish_agent(
                    db,
                    tenant_id=ctx.tenant_id,
                    actor_id=user.user_id,
                    agent_id=agent.id,
                    roles=list(user.roles),
                    request_id=ctx.request_id,
                )
            entry["ok"] = True
            entry["status"] = agent.status
            summary["applied"] += 1
            if agent.status == "published":
                summary["published"] += 1
            elif agent.status == "pending_platform_review":
                summary["pending_review"] += 1
            else:
                summary["draft"] += 1
        except AppError as e:
            entry["error"] = e.code
            summary["failed"] += 1
        results.append(entry)

    if payload.publish and summary["applied"] > 0:
        # 审批动作留痕：谁在什么时候点了采纳+发布
        run.approved_by = user.user_id
    log_audit(
        db,
        tenant_id=ctx.tenant_id,
        actor_id=user.user_id,
        action="factory_apply",
        resource_type="factory_run",
        resource_id=run.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"publish": payload.publish, "codes": payload.codes, **summary},
    )
    db.commit()
    return ok(
        {"run_id": run.id, "publish": payload.publish, "results": results, **summary}, request
    )
