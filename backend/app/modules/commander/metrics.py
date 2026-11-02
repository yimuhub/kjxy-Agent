"""指挥官运行指标聚合（§5.12，本地只读，不新增表）。

窗口按 ``commander_runs.created_at`` 计算：
- 澄清运行（``error_code=COMMANDER_NEEDS_INPUT``）不是失败也不是成功，整体排除；
- 计划成功率 = 非 fallback 且非澄清的 run 占比，fallback 率 = fallback run 占比；
- 子任务成功率按窗口内运行名下的子任务计；
- 平均并发 / 时长分布只取真正执行过的 completed/partial/failed 运行。

任何维度为空时返回 0 / 0.0，调用方无需处理除零。
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.agents.models import CREATED_FROM_COMMANDER, YardAgent
from app.modules.commander.models import (
    RUN_CANCELLED,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_PARTIAL,
    RUN_QUEUED,
    RUN_RUNNING,
    CommanderRun,
    CommanderSubtask,
)
from app.modules.llm.models import LLMCall

#: 澄清运行错误码（与 service.ERR_NEEDS_INPUT 同值；这里独立声明避免循环导入）
NEEDS_INPUT_CODE = "COMMANDER_NEEDS_INPUT"

_EXECUTED_STATUSES = frozenset({RUN_COMPLETED, RUN_PARTIAL, RUN_FAILED})


def _rate(part: int, total: int) -> float:
    return round(part / total, 4) if total else 0.0


def _percentile(values: list[int], percentile: int) -> int:
    """最近秩百分位（整数毫秒，避免浮点噪声）。"""
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile / 100 * len(ordered)) - 1)
    return int(ordered[index])


def stats(db: Session, yard_id: str, days: int = 30) -> dict[str, Any]:
    """聚合指定小院最近 ``days`` 天的指挥官运行指标。"""
    window_days = max(1, min(int(days), 365))
    cutoff = datetime.now(UTC) - timedelta(days=window_days)

    rows = list(
        db.scalars(
            select(CommanderRun).where(
                CommanderRun.yard_id == yard_id,
                CommanderRun.created_at >= cutoff,
            )
        )
    )
    runs = [run for run in rows if run.error_code != NEEDS_INPUT_CODE]
    run_ids = [run.id for run in runs]

    run_counts = {
        RUN_QUEUED: 0,
        RUN_RUNNING: 0,
        RUN_COMPLETED: 0,
        RUN_PARTIAL: 0,
        RUN_FAILED: 0,
        RUN_CANCELLED: 0,
    }
    fallback = 0
    executed: list[CommanderRun] = []
    for run in runs:
        if run.status in run_counts:
            run_counts[run.status] += 1
        if bool((run.plan_json or {}).get("fallback")):
            fallback += 1
        if run.status in _EXECUTED_STATUSES:
            executed.append(run)

    total = len(runs)
    subtasks: list[CommanderSubtask] = []
    if run_ids:
        subtasks = list(
            db.scalars(
                select(CommanderSubtask).where(
                    CommanderSubtask.yard_id == yard_id,
                    CommanderSubtask.run_id.in_(run_ids),
                )
            )
        )
    sub_counts = {"ok": 0, "error": 0, "skipped": 0}
    for subtask in subtasks:
        if subtask.status in sub_counts:
            sub_counts[subtask.status] += 1
    sub_total = len(subtasks)

    parallel_values = [int(run.effective_parallel or 0) for run in executed]
    latency_values = [int(run.latency_ms or 0) for run in executed]

    auto_created = db.scalar(
        select(func.count())
        .select_from(YardAgent)
        .where(
            YardAgent.yard_id == yard_id,
            YardAgent.created_from == CREATED_FROM_COMMANDER,
            YardAgent.created_at >= cutoff,
        )
    )

    return {
        "window_days": window_days,
        "runs": {"total": total, **run_counts},
        "plan": {
            "success_rate": _rate(total - fallback, total),
            "fallback_rate": _rate(fallback, total),
        },
        "subtasks": {
            "total": sub_total,
            "ok": sub_counts["ok"],
            "error": sub_counts["error"],
            "skipped": sub_counts["skipped"],
            "success_rate": _rate(sub_counts["ok"], sub_total),
        },
        "avg_effective_parallel": (
            round(sum(parallel_values) / len(parallel_values), 2) if parallel_values else 0.0
        ),
        "degraded_runs": sum(1 for run in runs if bool(run.degraded)),
        "auto_created_agents": int(auto_created or 0),
        "latency_ms": {
            "p50": _percentile(latency_values, 50),
            "p90": _percentile(latency_values, 90),
            "max": max(latency_values) if latency_values else 0,
        },
    }


def cost_stats(db: Session, yard_id: str, days: int = 30) -> dict[str, Any]:
    """按用途/模型聚合 LLM 调用成本，并给出指挥官运行级 token 总览。"""
    window_days = max(1, min(int(days), 365))
    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    rows = db.execute(
        select(
            func.coalesce(LLMCall.purpose, "platform").label("purpose"),
            LLMCall.model.label("model"),
            func.count().label("calls"),
            func.coalesce(func.sum(LLMCall.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(LLMCall.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(LLMCall.estimated_cost), 0.0).label("cost"),
        )
        .where(LLMCall.yard_id == yard_id, LLMCall.created_at >= cutoff)
        .group_by(func.coalesce(LLMCall.purpose, "platform"), LLMCall.model)
        .order_by(func.coalesce(func.sum(LLMCall.input_tokens), 0).desc())
    ).all()

    by_purpose: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}
    total = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "tokens": 0, "cost": 0.0}
    for purpose, model, calls, input_tokens, output_tokens, cost in rows:
        tokens = int(input_tokens or 0) + int(output_tokens or 0)
        item = {
            "calls": int(calls or 0),
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "tokens": tokens,
            "cost": round(float(cost or 0), 4),
        }
        purpose_item = by_purpose.setdefault(
            str(purpose), {"purpose": str(purpose), "calls": 0, "tokens": 0, "cost": 0.0}
        )
        purpose_item["calls"] += item["calls"]
        purpose_item["tokens"] += item["tokens"]
        purpose_item["cost"] = round(purpose_item["cost"] + item["cost"], 4)
        model_item = by_model.setdefault(
            str(model), {"model": str(model), "calls": 0, "tokens": 0, "cost": 0.0}
        )
        model_item["calls"] += item["calls"]
        model_item["tokens"] += item["tokens"]
        model_item["cost"] = round(model_item["cost"] + item["cost"], 4)
        for key in ("calls", "input_tokens", "output_tokens", "tokens"):
            total[key] += item[key]
        total["cost"] = round(total["cost"] + item["cost"], 4)

    runs = list(
        db.scalars(
            select(CommanderRun).where(
                CommanderRun.yard_id == yard_id,
                CommanderRun.created_at >= cutoff,
            )
        )
    )
    return {
        "window_days": window_days,
        "total": total,
        "by_purpose": sorted(by_purpose.values(), key=lambda x: x["tokens"], reverse=True),
        "by_model": sorted(by_model.values(), key=lambda x: x["tokens"], reverse=True),
        "commander": {
            "runs": len(runs),
            "tokens": sum(int(run.tokens_total or 0) for run in runs),
            "completed": sum(1 for run in runs if run.status == RUN_COMPLETED),
            "failed": sum(1 for run in runs if run.status in (RUN_FAILED, RUN_PARTIAL)),
        },
    }


__all__ = ["NEEDS_INPUT_CODE", "cost_stats", "stats"]
