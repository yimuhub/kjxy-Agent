"""指挥官计划编辑、重试、取消与并发闸门（从 commander/service.py 拆分，保持对外重导出兼容）。"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.timezone import utc_now as _now
from app.modules.agents.models import (
    CREATED_FROM_COMMANDER,
    STATUS_DELETED,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    YardAgent,
)
from app.modules.audit.service import log_audit
from app.modules.commander import deliverables, feasibility
from app.modules.commander import settings as commander_settings
from app.modules.commander.models import (
    RUN_CANCELLED,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_QUEUED,
    RUN_RUNNING,
    SOURCE_COMMANDER,
    SOURCE_EXISTING,
    SOURCE_GENERATED,
    SUB_ERROR,
    SUB_PENDING,
    SUB_SKIPPED,
    CommanderRun,
    CommanderSubtask,
)
from app.modules.commander.views import get_run, require_run_access

log = get_logger(__name__)

ERR_RUN_BUSY = "COMMANDER_RUN_BUSY"
ERR_RUN_CANCELLED = "COMMANDER_RUN_CANCELLED"
ERR_RUN_STATE = "COMMANDER_RUN_STATE"


class ConcurrencyGate:
    """可动态缩容的并发闸门（降级时把容量改小，不影响已运行任务）。"""

    def __init__(self, capacity: int) -> None:
        self.capacity = max(1, int(capacity))
        self._inflight = 0
        self._condition = asyncio.Condition()

    async def acquire(self) -> None:
        async with self._condition:
            await self._condition.wait_for(lambda: self._inflight < self.capacity)
            self._inflight += 1

    async def release(self) -> None:
        async with self._condition:
            self._inflight = max(0, self._inflight - 1)
            self._condition.notify_all()

    async def set_capacity(self, capacity: int) -> None:
        async with self._condition:
            self.capacity = max(1, int(capacity))
            self._condition.notify_all()

    async def __aenter__(self) -> ConcurrencyGate:
        await self.acquire()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.release()


def _unrealizable_plan(text: str, check: feasibility.Assessment) -> dict[str, Any]:
    """现实不可执行时的唯一合法降级：如实说明 + 给出可照着做的替代路径。"""
    verb = check.verb or "该动作"
    guidance = check.guidance or (
        f"「{verb}」需要人在现实里完成，我无法代替你的身体动作。"
        "我可以把该做的事整理成一份可执行的清单，你照着做即可。"
    )
    return {
        "goal": (text or "完成用户请求")[:500],
        "success_criteria": [],
        "assumptions": [],
        "merge_notes": "",
        "fallback": True,
        "fallback_reason": feasibility.REASON_UNREALIZABLE,
        "feasibility": check.to_json(),
        "subtasks": [
            {
                "seq": 1,
                "title": "说明原因与替代方案",
                "instruction": (
                    f"用户目标：{text}\n"
                    f"该目标需要现实中的身体动作（{verb}），系统无法执行。\n"
                    f"已知的替代路径参考：{guidance}\n"
                    "请直接回答：1) 为什么做不到（一句话）；2) 你能替他做到哪一步"
                    "（查资料/写清单/出方案）；3) 他照着做时需要准备什么、分几步。"
                    "不要假装已经完成，也不要只回一句「无法完成」。"
                ),
                "expected_output": "原因说明 + 可直接照做的替代清单",
                "agent_hint": "指挥官",
                "agent_code": "",
                "depends_on": [],
                "executor": "commander",
            }
        ],
        "clarify": [],
    }


def _bind_plan_agent(db: Session, ctx: YardContext, agent_id: str | None) -> dict[str, Any]:
    """校验执行者绑定：空 = 指挥官代执行；否则必须可见可用。"""
    if not agent_id:
        return {
            "agent_id": None,
            "agent_code": None,
            "agent_name": "指挥官",
            "agent_source": SOURCE_COMMANDER,
        }
    agent = db.get(YardAgent, str(agent_id))
    if agent is None or agent.yard_id != ctx.yard_id or agent.status == STATUS_DELETED:
        raise AppError(
            "INVALID_REQUEST",
            "所选助手不存在、不属于当前小院或已被删除。",
            http_status=400,
        )
    visible = agent.visibility == "public" or agent.created_by == ctx.user_id
    if not visible:
        raise AppError(
            "INVALID_REQUEST",
            f"无权使用助手「{agent.name}」（仅创建者可用）。",
            http_status=400,
        )
    generated_draft = agent.created_from == CREATED_FROM_COMMANDER and agent.status == STATUS_DRAFT
    if agent.status != STATUS_PUBLISHED and not generated_draft:
        raise AppError(
            "INVALID_REQUEST",
            f"助手「{agent.name}」当前状态为 {agent.status}，"
            "只有已发布助手或指挥官生成的草稿可用。",
            http_status=400,
        )
    return {
        "agent_id": agent.id,
        "agent_code": agent.agent_code,
        "agent_name": agent.name,
        "agent_source": SOURCE_GENERATED if generated_draft else SOURCE_EXISTING,
    }


def _edit_depends_on(raw: Any, *, position: int) -> list[int]:
    """按新数组位置（1-based）解释依赖，只保留指向更早位置的合法项。"""
    if not isinstance(raw, list):
        return []
    deps: list[int] = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, int):
            continue
        if 1 <= item < position and item not in deps:
            deps.append(item)
    return deps


def update_plan(
    db: Session, ctx: YardContext, run_id: str, subtasks_payload: list[dict[str, Any]]
) -> CommanderRun:
    """用计划卡提交的内容整表替换子任务（仅 queued 运行可编辑）。"""
    from app.modules.commander import service as _svc

    run, existing = get_run(db, ctx.yard_id, run_id)
    require_run_access(db, run, user_id=ctx.user_id, roles=ctx.roles)
    if run.status != RUN_QUEUED:
        raise AppError(
            ERR_RUN_STATE,
            f"当前状态为 {run.status}，只有排队中的运行可以编辑计划。",
            http_status=409,
        )

    settings = commander_settings.settings_for(db, run.yard_id)
    limit = int(settings["max_subtasks"])
    if not 1 <= len(subtasks_payload) <= limit:
        raise AppError(
            "INVALID_REQUEST",
            f"子任务数量需在 1-{limit} 之间。",
            http_status=400,
        )

    plan_subtasks: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    for position, item in enumerate(subtasks_payload, start=1):
        title = str(item.get("title") or "").strip()
        instruction = str(item.get("instruction") or "").strip()
        if not title or len(title) > 200:
            raise AppError(
                "INVALID_REQUEST",
                f"第 {position} 个子任务标题不能为空且不超过 200 字。",
                http_status=400,
            )
        if not instruction or len(instruction) > 8000:
            raise AppError(
                "INVALID_REQUEST",
                f"第 {position} 个子任务指令不能为空且不超过 8000 字。",
                http_status=400,
            )
        binding = _bind_plan_agent(db, ctx, item.get("agent_id"))
        deps = _edit_depends_on(item.get("depends_on"), position=position)
        bindings.append(binding)
        plan_subtasks.append(
            {
                "seq": position,
                "title": title,
                "instruction": instruction,
                "expected_output": str(item.get("expected_output") or "").strip()[:1000],
                "agent_hint": binding["agent_name"] or "",
                "agent_code": binding["agent_code"] or "",
                "depends_on": deps,
            }
        )

    removed = max(0, len(existing) - len(plan_subtasks))
    required_fmt = deliverables.detect(str(run.goal or "")) or str(
        (run.plan_json or {}).get("deliverable", {}).get("format") or ""
    )
    if required_fmt:
        before = len(plan_subtasks)
        temp = {"subtasks": plan_subtasks}
        if deliverables.ensure_plan_deliverable(
            temp, str(run.goal or ""), required_fmt, max_subtasks=limit
        ):
            plan_subtasks = temp["subtasks"]
            commander_binding = {
                "agent_id": None,
                "agent_code": None,
                "agent_name": "指挥官",
                "agent_source": SOURCE_COMMANDER,
            }
            if len(plan_subtasks) > before:
                bindings.append(commander_binding)
            else:
                bindings[-1] = commander_binding
            log.info(
                "commander plan edited: deliverable=%s auto_restored=%s",
                required_fmt,
                len(plan_subtasks) > before,
            )
    for row in existing:
        db.delete(row)
    db.flush()

    new_rows: list[CommanderSubtask] = []
    for plan_subtask, binding in zip(plan_subtasks, bindings, strict=True):
        row = CommanderSubtask(
            run_id=run.id,
            yard_id=ctx.yard_id,
            seq=int(plan_subtask["seq"]),
            title=str(plan_subtask["title"])[:200],
            instruction=str(plan_subtask["instruction"]),
            expected_output=str(plan_subtask["expected_output"]),
            agent_id=binding["agent_id"],
            agent_code=binding["agent_code"],
            agent_name=binding["agent_name"],
            agent_source=binding["agent_source"],
            status=SUB_PENDING,
            depends_on_json=list(plan_subtask["depends_on"]),
            attempts=0,
            input_tokens=0,
            output_tokens=0,
        )
        db.add(row)
        new_rows.append(row)

    plan = dict(run.plan_json or {})
    plan.setdefault("clarify", [])
    plan["subtasks"] = plan_subtasks
    if required_fmt:
        plan["deliverable"] = {
            "format": required_fmt,
            "label": deliverables.label(required_fmt),
        }
    plan["generated_agents"] = [
        {
            "seq": int(plan_subtask["seq"]),
            "agent_id": binding["agent_id"],
            "agent_code": binding["agent_code"],
            "agent_name": binding["agent_name"],
        }
        for plan_subtask, binding in zip(plan_subtasks, bindings, strict=True)
        if binding["agent_source"] == SOURCE_GENERATED
    ]
    run.plan_json = plan
    db.add(run)
    db.flush()

    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="commander_plan_edited",
        resource_type="commander_run",
        resource_id=run.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"subtasks": len(new_rows), "deleted": removed},
    )
    _svc._safe_commit(db)
    return run


def retry_run(db: Session, ctx: YardContext, run_id: str) -> CommanderRun:
    """把 error/skipped 子任务重置 pending（attempts 不清零），运行回到 queued。"""
    from app.modules.commander import service as _svc

    run, subtasks = get_run(db, ctx.yard_id, run_id)
    require_run_access(db, run, user_id=ctx.user_id, roles=ctx.roles)
    if run.status == RUN_RUNNING:
        raise AppError(ERR_RUN_BUSY, "运行正在执行中，请先取消或等待完成。", http_status=409)
    if run.status == RUN_CANCELLED:
        raise AppError(ERR_RUN_STATE, "已取消的运行不能重试。", http_status=409)

    retried = 0
    reclaimed = 0
    for st in subtasks:
        if st.status in (SUB_ERROR, SUB_SKIPPED):
            reclaimed += int(st.input_tokens or 0) + int(st.output_tokens or 0)
            st.status = SUB_PENDING
            st.error_code = None
            st.output_excerpt = None
            st.artifact_json = None
            st.input_tokens = 0
            st.output_tokens = 0
            st.started_at = None
            st.finished_at = None
            db.add(st)
            retried += 1
    if reclaimed:
        run.tokens_total = max(0, int(run.tokens_total or 0) - reclaimed)
        run.budget_status = ""
    if retried == 0:
        raise AppError(
            "COMMANDER_NOTHING_TO_RETRY",
            "没有失败或跳过的子任务可重试。",
            http_status=400,
        )
    settings = commander_settings.settings_for(db, run.yard_id)
    run.status = RUN_QUEUED
    run.error_code = None
    run.degraded = False
    run.max_parallel = int(settings["max_parallel"])
    run.effective_parallel = int(settings["max_parallel"])
    db.add(run)
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="commander_subtask_retried",
        resource_type="commander_run",
        resource_id=run.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"retried": retried},
    )
    _svc._safe_commit(db)
    return run


def cancel_run(db: Session, ctx: YardContext, run_id: str) -> CommanderRun:
    """取消：pending → skipped，已完成的保留，运行标记 cancelled。"""
    from app.modules.commander import service as _svc

    run, subtasks = get_run(db, ctx.yard_id, run_id)
    require_run_access(db, run, user_id=ctx.user_id, roles=ctx.roles)
    if run.status in (RUN_COMPLETED, RUN_FAILED, RUN_CANCELLED):
        raise AppError(ERR_RUN_STATE, "该运行已结束，无法取消。", http_status=409)

    now = _now()
    skipped = 0
    for st in subtasks:
        if st.status == SUB_PENDING:
            st.status = SUB_SKIPPED
            st.error_code = ERR_RUN_CANCELLED
            st.finished_at = now
            db.add(st)
            skipped += 1
    run.status = RUN_CANCELLED
    run.error_code = ERR_RUN_CANCELLED
    db.add(run)
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="commander_run_cancelled",
        resource_type="commander_run",
        resource_id=run.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"skipped": skipped},
    )
    _svc._safe_commit(db)
    return run


__all__ = [
    "ConcurrencyGate",
    "ERR_RUN_BUSY",
    "ERR_RUN_CANCELLED",
    "ERR_RUN_STATE",
    "_bind_plan_agent",
    "_edit_depends_on",
    "_unrealizable_plan",
    "cancel_run",
    "retry_run",
    "update_plan",
]
