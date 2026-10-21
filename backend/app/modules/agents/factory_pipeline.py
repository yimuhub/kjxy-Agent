"""Agent 工厂流水线：agent_factory 任务处理器（M3）。

职责（task_type=agent_factory，经 tasks.handlers 注册，Celery/本机线程通用）：
1. planning：调 LLM 产蓝图 JSON → 解析（容忍围栏）；任何失败落内置启发式
   兜底，generated_by 诚实标注 llm|heuristic；agent_code 统一加 run 短后缀防撞码。
2. generating：每个蓝图角色用 knowledge.service.search 以建议问题探测本租户
   知识库覆盖（knowledge_coverage / needs_knowledge / smoke_questions），
   并创建 TenantAgent 草稿（created_from=factory，risk 走 assess_risk）。
3. completed：蓝图（含 agent_id/coverage/needs_knowledge/smoke_questions）回写
   factory_runs.blueprint_json；任何异常 → run 置 failed + error，绝不伪报。

里程碑机制：每个阶段既更新 factory_runs.status，又往 task_events 写一条
event_type="milestone" 的事件（payload.milestone）并单独 commit —— 与
tasks/stream.py 的 SSE 追尾轮询读的是同一张 TaskEvent 表，工厂页订阅
GET /tasks/{task_id}/events/stream 即可实时看到 planning/generating/completed。
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import TenantContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.agents import declarative
from app.modules.agents import factory_blueprint as fb
from app.modules.agents.declarative import DeclarativeAgentCreate
from app.modules.agents.factory_models import (
    GENERATED_BY_HEURISTIC,
    GENERATED_BY_LLM,
    STATUS_CLAIMED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_GENERATING,
    STATUS_PLANNING,
    FactoryRun,
)
from app.modules.audit.service import log_audit
from app.modules.tasks.handlers import register_handler
from app.modules.tasks.models import Task
from app.modules.tasks.service import TaskService
from app.modules.tasks.worker_tasks import _get_session_factory

log = get_logger(__name__)

FACTORY_TASK_TYPE = "agent_factory"

#: 覆盖探测：每个角色最多探测的建议问题数 / 单次检索 top_k
COVERAGE_MAX_PROBES = 3
COVERAGE_TOP_K = 3

#: run 重跑保护：仅 claimed/failed 可被流水线领取（completed 拒绝重复装配）
_RERUN_OK = (STATUS_CLAIMED, STATUS_FAILED)


def _milestone(
    db: Session,
    run: FactoryRun | None,
    task_id: str,
    milestone: str,
    message: str,
    *,
    progress: float | None = None,
    run_status: str | None = None,
) -> None:
    """写一个工厂里程碑：run.status 推进 + task_events 里程碑事件 + commit。

    里程碑事件正是 SSE（tasks/stream.py）追尾读取的 TaskEvent 行；单独 commit
    保证执行中每阶段对订阅者立即可见。run 为 None（run 缺失的失败路径）只写事件。
    """
    if run is not None and run_status is not None:
        run.status = run_status
    task = db.get(Task, task_id)
    if task is not None:
        TaskService._add_event(
            db,
            task,
            "milestone",
            message,
            progress=progress,
            payload={"milestone": milestone, "run_id": run.id if run else None},
        )
    db.commit()


def _resolve_blueprint_items(
    db: Session,
    *,
    run: FactoryRun,
    task_id: str,
    industry_brief: str,
    focus: str | None,
    request_id: str,
) -> tuple[list[dict[str, Any]], str]:
    """planning 阶段：LLM 蓝图优先，解析/调用失败落启发式；返回 (角色列表, 来源)。

    工厂 run 纳入 agent_run_steps 同表 checkpoint（P0 内核）：LLM 蓝图调用
    落一行 llm_call 步（ok/error），供 GET steps 与 SSE 步骤流统一观察。
    """
    import time

    from app.modules.agents import run_steps as rs
    from app.modules.llm.schemas import ChatMessage, LLMMetadata, LLMRequest
    from app.modules.llm.service import complete_with_logging

    started = time.perf_counter()
    try:
        messages = fb.build_blueprint_messages(industry_brief, focus)
        request = LLMRequest(
            messages=[ChatMessage(role=m["role"], content=m["content"]) for m in messages],
            metadata=LLMMetadata(tenant_id=run.tenant_id, task_id=task_id, request_id=request_id),
        )

        async def _call() -> str:
            resp = await complete_with_logging(db, request)
            return resp.content

        raw = asyncio.run(_call())
        items = fb.parse_blueprint(raw)
        generated_by = GENERATED_BY_LLM
        rs.record_step(
            db,
            tenant_id=run.tenant_id,
            run_id=run.id,
            step_type=rs.STEP_LLM,
            status=rs.STEP_OK,
            input_json={"stage": "blueprint", "task_id": task_id},
            output_json={
                "content_chars": len(raw),
                "roles": len(items),
                "model": getattr(request, "model", None) or "resolved-by-provider",
            },
            duration_ms=int((time.perf_counter() - started) * 1000),
            commit=False,  # 与紧随其后的 blueprint_ready 里程碑同一事务提交
        )
        log.info("factory run %s: blueprint from LLM, %d roles", run.id[:8], len(items))
    except Exception as e:
        # 诚实兜底：LLM 不可用或输出不可解析 → 内置启发式蓝图（不伪报为 llm）
        rs.record_step(
            db,
            tenant_id=run.tenant_id,
            run_id=run.id,
            step_type=rs.STEP_LLM,
            status=rs.STEP_ERROR,
            input_json={"stage": "blueprint", "task_id": task_id},
            duration_ms=int((time.perf_counter() - started) * 1000),
            error_text=f"{type(e).__name__}: {e}",
            commit=False,
        )
        items = fb.heuristic_blueprint(industry_brief, focus)
        generated_by = GENERATED_BY_HEURISTIC
        log.info(
            "factory run %s: LLM blueprint unavailable (%s), heuristic fallback used",
            run.id[:8],
            e,
        )
    return fb.finalize_codes(items, task_id or run.id), generated_by


def _probe_coverage(
    db: Session, tenant_id: str, created_by: str, questions: list[str], request_id: str
) -> tuple[int, list[str]]:
    """以建议问题探测租户知识库覆盖：返回 (最大命中数, 实际探测的问题)。

    检索失败按 0 命中处理（needs_knowledge 宁严勿松），并诚实记日志。
    """
    from app.modules.knowledge.service import search as knowledge_search

    probes = [q for q in questions if str(q).strip()][:COVERAGE_MAX_PROBES]
    if not probes:
        return 0, []
    ctx = TenantContext(
        tenant_id=tenant_id,
        user_id=created_by or "factory",
        roles=["tenant_admin"],
        request_id=request_id,
    )
    coverage = 0
    for q in probes:
        try:
            hits = knowledge_search(db, ctx, str(q), COVERAGE_TOP_K)
            coverage = max(coverage, len(hits))
        except Exception as e:
            log.warning("factory coverage probe failed (t=%s): %s", tenant_id, e)
            coverage = max(coverage, 0)
    return coverage, probes


def _create_draft(
    db: Session, *, run: FactoryRun, item: dict[str, Any], actor_id: str, request_id: str
) -> declarative.TenantAgent:
    """蓝图角色 → TenantAgent 草稿（created_from=factory；同码冲突自动升版本重试）。"""
    last_error: AppError | None = None
    for patch in range(3):
        req = DeclarativeAgentCreate(
            agent_code=str(item["agent_code"]),
            name=str(item["name"])[:100],
            description=str(item.get("description") or ""),
            emoji=str(item.get("emoji") or ""),
            persona_prompt=str(item["persona_prompt"]),
            task_prompt_template=str(item["task_prompt_template"]),
            allowed_tools=list(item.get("allowed_tools") or []),
            knowledge_scopes=list(item.get("knowledge_scopes") or []),
            created_from="factory",
        )
        try:
            return declarative.create_agent(
                db,
                tenant_id=run.tenant_id,
                actor_id=actor_id,
                req=req,
                request_id=request_id,
                version=f"1.0.{patch}",
            )
        except AppError as e:
            if e.code != "AGENT_VERSION_CONFLICT":
                raise
            last_error = e
    assert last_error is not None
    raise last_error


def run_agent_factory(input_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """agent_factory 任务处理器：蓝图生成 → 覆盖探测 + 草稿装配 → 蓝图回写。

    成功返回装配摘要（写 task.result_json）；失败先把 run 置 failed + error
    并写 failed 里程碑，再抛 AppError 让 tasks worker 如实记 task failed。
    """
    industry_brief = str((input_data or {}).get("industry_brief") or "").strip()
    focus = str((input_data or {}).get("focus") or "").strip() or None
    run_id = str((input_data or {}).get("run_id") or "").strip()
    task_id = str(context.get("task_id") or "")
    request_id = str(context.get("request_id") or "req_unknown")
    if not industry_brief or not run_id:
        raise AppError(
            "INVALID_REQUEST", "industry_brief 与 run_id 为工厂任务的必填输入。", http_status=400
        )

    db = _get_session_factory()()
    try:
        run = db.get(FactoryRun, run_id)
        if run is None or run.tenant_id != context.get("tenant_id"):
            raise AppError("FACTORY_RUN_NOT_FOUND", f"工厂运行不存在：{run_id}。", http_status=404)
        if run.status not in _RERUN_OK:
            raise AppError(
                "FACTORY_RUN_STATE",
                f"运行当前状态（{run.status}）不允许（重复）执行。",
                http_status=409,
            )

        # -- 里程碑 1：planning（LLM/启发式蓝图规划）-------------------------
        _milestone(
            db,
            run,
            task_id,
            "planning",
            "工厂开始规划 Agent 蓝图（LLM 优先，失败落内置启发式）。",
            progress=0.1,
            run_status=STATUS_PLANNING,
        )
        items, generated_by = _resolve_blueprint_items(
            db,
            run=run,
            task_id=task_id,
            industry_brief=industry_brief,
            focus=focus,
            request_id=request_id,
        )
        payload = fb.build_blueprint_payload(industry_brief, focus, generated_by, items)
        run.blueprint_json = payload
        run.generated_by = generated_by
        _milestone(
            db,
            run,
            task_id,
            "blueprint_ready",
            f"蓝图就绪：{len(items)} 个角色（来源 {generated_by}）。",
            progress=0.35,
        )

        # -- 里程碑 2：generating（覆盖探测 + TenantAgent 草稿装配）----------
        _milestone(
            db,
            run,
            task_id,
            "generating",
            "开始探测知识库覆盖并创建 Agent 草稿（created_from=factory）。",
            progress=0.6,
            run_status=STATUS_GENERATING,
        )
        actor_id = run.created_by or str(context.get("actor_id") or "factory")
        for item in items:
            coverage, probes = _probe_coverage(
                db, run.tenant_id, actor_id, list(item.get("suggested_questions") or []), request_id
            )
            agent = _create_draft(db, run=run, item=item, actor_id=actor_id, request_id=request_id)
            item["knowledge_coverage"] = coverage
            item["needs_knowledge"] = coverage == 0
            item["smoke_questions"] = probes
            item["agent_id"] = agent.id
            item["risk_level"] = agent.risk_level
            item["status"] = agent.status
        db.commit()  # 草稿与蓝图更新同事务提交（失败整体回滚，不留半成品）

        # -- 里程碑 3：completed（蓝图回写收口）------------------------------
        run.blueprint_json = fb.build_blueprint_payload(industry_brief, focus, generated_by, items)
        needs_kb = sum(1 for i in items if i.get("needs_knowledge"))
        # checkpoint：工厂 run 的 final 步（与 completed 里程碑同事务提交）
        from app.modules.agents import run_steps as rs

        rs.record_step(
            db,
            tenant_id=run.tenant_id,
            run_id=run.id,
            step_type=rs.STEP_FINAL,
            status=rs.STEP_OK,
            output_json={
                "generated_by": generated_by,
                "role_count": len(items),
                "needs_knowledge_count": needs_kb,
                "truncated": False,
            },
            commit=False,
        )
        _milestone(
            db,
            run,
            task_id,
            "completed",
            f"工厂装配完成：{len(items)} 个草稿，其中 {needs_kb} 个知识缺口。",
            progress=1.0,
            run_status=STATUS_COMPLETED,
        )
        log_audit(
            db,
            tenant_id=run.tenant_id,
            actor_id=actor_id,
            action="factory_run_completed",
            resource_type="factory_run",
            resource_id=run.id,
            result="success",
            request_id=request_id,
            metadata={
                "generated_by": generated_by,
                "role_count": len(items),
                "needs_knowledge": needs_kb,
            },
        )
        db.commit()
        return {
            "run_id": run.id,
            "generated_by": generated_by,
            "role_count": len(items),
            "needs_knowledge_count": needs_kb,
            "agents": [
                {
                    "agent_code": i["agent_code"],
                    "agent_id": i.get("agent_id"),
                    "knowledge_coverage": i.get("knowledge_coverage"),
                    "needs_knowledge": i.get("needs_knowledge"),
                    "risk_level": i.get("risk_level"),
                    "status": i.get("status"),
                }
                for i in items
            ],
        }
    except Exception as exc:
        # 失败路径（不伪报）：回滚草稿事务 → run 置 failed + error + failed 里程碑，
        # 再把异常抛给 tasks worker（worker 侧如实记 task failed）。
        detail = exc.message if isinstance(exc, AppError) else str(exc) or repr(exc)
        log.exception("agent_factory task %s failed", task_id[:8])
        try:
            db.rollback()
            run = db.get(FactoryRun, run_id)
            if run is not None and run.tenant_id == context.get("tenant_id"):
                run.status = STATUS_FAILED
                run.error = str(detail)[:2000]
                # checkpoint：工厂 run 失败终态步（SSE 据此收流）
                from app.modules.agents import run_steps as rs

                rs.record_step(
                    db,
                    tenant_id=run.tenant_id,
                    run_id=run.id,
                    step_type=rs.STEP_FINAL,
                    status=rs.STEP_ERROR,
                    error_text=str(detail)[:2000],
                    commit=False,
                )
            _milestone(
                db,
                run if run is not None and run.tenant_id == context.get("tenant_id") else None,
                task_id,
                "failed",
                f"工厂运行失败：{detail}",
            )
        except Exception:
            db.rollback()
            log.exception("agent_factory failure bookkeeping failed (task %s)", task_id[:8])
        if isinstance(exc, AppError):
            raise
        raise AppError(
            "FACTORY_RUN_FAILED", f"Agent 工厂执行失败：{detail}", http_status=502
        ) from exc
    finally:
        db.close()


register_handler(FACTORY_TASK_TYPE)(run_agent_factory)
log.info("task handler registered: %s", FACTORY_TASK_TYPE)
