"""指挥官进化报告：每日复盘、周优化报告与月度迭代。

全部确定性生成，不额外调用 LLM：
- 日报告：当日运行、失败、成本与资产变化；
- 周优化：当周测评 + 资产增量 + 成本对比 + 下周建议；
- 月度迭代：整月指标、版本号、设置快照与可回滚记录。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import timezone as business_time
from app.core.deps import YardContext
from app.modules.commander import evaluator, snapshots
from app.modules.commander import settings as commander_settings
from app.modules.commander.asset_models import (
    FAILURE_ACTIVE,
    SKILL_ACTIVE,
    WORKFLOW_ACTIVE,
    CommanderFailureCase,
    CommanderSkillSnippet,
    CommanderWorkflow,
)
from app.modules.commander.evolution_models import CommanderIteration
from app.modules.commander.governance_models import CommanderEvaluation
from app.modules.commander.models import (
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_PARTIAL,
    SUB_ERROR,
    SUB_OK,
    CommanderRun,
    CommanderSubtask,
)
from app.modules.llm.models import LLMCall


def _now() -> datetime:
    return datetime.now(UTC)


def _period(start: datetime, end: datetime) -> dict[str, str]:
    return {"start": start.isoformat(), "end": end.isoformat()}


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    """按业务时区切日界（库里时间戳仍是 UTC）：UTC 日界会让北京时间 0–8 点的日报报告「昨天」。"""
    return business_time.day_bounds(day)


def _month_bounds(month_key: str) -> tuple[datetime, datetime]:
    return business_time.month_bounds(month_key)


def _previous_month(today: date | None = None) -> str:
    current = today or business_time.business_today()
    first = current.replace(day=1)
    prev = first - timedelta(days=1)
    return f"{prev.year:04d}-{prev.month:02d}"


def _aggregate(db: Session, yard_id: str, start: datetime, end: datetime) -> dict[str, Any]:
    runs = list(
        db.scalars(
            select(CommanderRun).where(
                CommanderRun.yard_id == yard_id,
                CommanderRun.created_at >= start,
                CommanderRun.created_at < end,
            )
        )
    )
    run_ids = [run.id for run in runs]
    subtasks: list[CommanderSubtask] = []
    if run_ids:
        subtasks = list(
            db.scalars(select(CommanderSubtask).where(CommanderSubtask.run_id.in_(run_ids)))
        )
    ok = sum(1 for st in subtasks if st.status == SUB_OK)
    failed = [st for st in subtasks if st.status == SUB_ERROR]
    error_codes: dict[str, int] = {}
    for st in failed:
        code = str(st.error_code or "UNKNOWN")
        error_codes[code] = error_codes.get(code, 0) + 1
    tokens = sum(int(run.tokens_total or 0) for run in runs)
    latencies = [int(run.latency_ms or 0) for run in runs if run.latency_ms]
    cost_row = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(LLMCall.input_tokens), 0),
            func.coalesce(func.sum(LLMCall.output_tokens), 0),
            func.coalesce(func.sum(LLMCall.estimated_cost), 0.0),
        ).where(
            LLMCall.yard_id == yard_id,
            LLMCall.created_at >= start,
            LLMCall.created_at < end,
        )
    ).one()
    status_counts: dict[str, int] = {}
    for run in runs:
        status_counts[run.status] = status_counts.get(run.status, 0) + 1
    return {
        "runs": len(runs),
        "status_counts": status_counts,
        "completed": sum(1 for run in runs if run.status in (RUN_COMPLETED, RUN_PARTIAL)),
        "failed_runs": sum(1 for run in runs if run.status == RUN_FAILED),
        "subtasks": len(subtasks),
        "ok": ok,
        "failed": len(failed),
        "success_rate": round(ok / len(subtasks), 4) if subtasks else 0.0,
        "error_codes": dict(sorted(error_codes.items(), key=lambda item: -item[1])),
        "tokens": tokens,
        "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
        "llm_calls": int(cost_row[0] or 0),
        "llm_input_tokens": int(cost_row[1] or 0),
        "llm_output_tokens": int(cost_row[2] or 0),
        "llm_cost": round(float(cost_row[3] or 0), 4),
    }


def _asset_delta(db: Session, yard_id: str, start: datetime, end: datetime) -> dict[str, int]:
    return {
        "workflows_created": db.query(CommanderWorkflow)
        .filter(
            CommanderWorkflow.yard_id == yard_id,
            CommanderWorkflow.created_at >= start,
            CommanderWorkflow.created_at < end,
        )
        .count(),
        "skills_created": db.query(CommanderSkillSnippet)
        .filter(
            CommanderSkillSnippet.yard_id == yard_id,
            CommanderSkillSnippet.created_at >= start,
            CommanderSkillSnippet.created_at < end,
        )
        .count(),
        "failures_seen": db.query(CommanderFailureCase)
        .filter(
            CommanderFailureCase.yard_id == yard_id,
            CommanderFailureCase.last_seen_at >= start,
            CommanderFailureCase.last_seen_at < end,
        )
        .count(),
    }


def _delta_line(current: int, previous: int, unit: str = "") -> str:
    diff = current - previous
    sign = "+" if diff > 0 else ""
    return f"{current}{unit}（较上期 {sign}{diff}{unit}）"


def daily_report(db: Session, *, yard_id: str, day: date) -> dict[str, Any]:
    start, end = _day_bounds(day)
    data = _aggregate(db, yard_id, start, end)
    assets = _asset_delta(db, yard_id, start, end)
    lines = [
        f"# 每日复盘 {day.isoformat()}",
        "",
        "## 一、任务汇总",
        f"- 运行：{data['runs']} 次；完成/部分完成 {data['completed']} 次，"
        f"失败 {data['failed_runs']} 次。",
        f"- 子任务：{data['ok']} / {data['subtasks']} 成功，成功率 "
        f"{round(data['success_rate'] * 100)}%。",
        f"- Token：{data['tokens']:,}；平均耗时 {data['avg_latency_ms']} ms。",
        "",
        "## 二、失败清单",
    ]
    if data["error_codes"]:
        lines.extend(f"- {code}：{count} 次" for code, count in data["error_codes"].items())
    else:
        lines.append("- 无失败子任务。")
    lines.extend(
        [
            "",
            "## 三、成本统计",
            f"- 模型调用 {data['llm_calls']} 次；输入/输出 "
            f"{data['llm_input_tokens']:,}/{data['llm_output_tokens']:,} tokens；"
            f"估算费用 ¥{data['llm_cost']:.4f}。",
            "",
            "## 四、资产变化",
            f"- 新增工作流 {assets['workflows_created']} 个；"
            f"新增技能 {assets['skills_created']} 个；"
            f"失败场景更新 {assets['failures_seen']} 个。",
            "",
            "## 五、优化标记",
        ]
    )
    if data["failed"]:
        lines.append("- 有失败步骤：优先查看失败黑名单，避免明天原样重试。")
    if data["success_rate"] >= 0.8 and data["subtasks"]:
        lines.append("- 成功率稳定：可把今日成功路径沉淀为工作流，提高下周复用率。")
    if data["llm_cost"] > 0 and data["runs"]:
        per_run = data["llm_cost"] / max(1, data["runs"])
        lines.append(f"- 单次运行平均成本 ¥{per_run:.4f}，超过日常基线时先砍冗余上下文。")
    if not data["runs"]:
        lines.append("- 今日无运行，无需调整；保持观察。")
    return {
        "day": day.isoformat(),
        "period": _period(start, end),
        "markdown": "\n".join(lines),
        "metrics": {**data, "assets": assets},
    }


def _week_bounds(week_key: str) -> tuple[datetime, datetime]:
    year_text, week_text = week_key.split("-W", 1)
    monday = date.fromisocalendar(int(year_text), int(week_text), 1)
    return business_time.week_bounds(monday)


def weekly_optimization(
    db: Session, *, yard_id: str, week_key: str | None = None
) -> dict[str, Any]:
    current_week = week_key or business_time.business_week_key()
    start, end = _week_bounds(current_week)
    prev_start = start - timedelta(days=7)
    data = _aggregate(db, yard_id, start, end)
    previous = _aggregate(db, yard_id, prev_start, start)
    assets = _asset_delta(db, yard_id, start, end)
    evaluation = db.scalar(
        select(CommanderEvaluation).where(
            CommanderEvaluation.yard_id == yard_id,
            CommanderEvaluation.week_key == current_week,
        )
    )
    persona = commander_settings.settings_for(db, yard_id).get("persona", "balanced")
    modules = commander_settings.settings_for(db, yard_id).get("prompt_modules") or []
    enabled_modules = ", ".join(str(item) for item in modules) or "无"
    cost_delta = _delta_line(round(data["llm_cost"], 4), round(previous["llm_cost"], 4))
    lines = [
        f"# 每周优化报告 {current_week}",
        "",
        f"周期：{start.date()} → {(end - timedelta(days=1)).date()}",
        "",
        "## 一、测评结论",
    ]
    if evaluation is not None:
        out = evaluator.evaluation_out(evaluation)
        lines.extend(
            [
                f"- 人格判定：{out['persona_label']}；综合分 {out['total']}。",
                f"- 结论：{out['summary']}",
            ]
        )
        if out["shortfalls"]:
            lines.append(f"- 主要短板：{'；'.join(out['shortfalls'][:3])}")
        if out["recommendations"]:
            lines.append(f"- 改进建议：{'；'.join(out['recommendations'][:3])}")
    else:
        lines.append("- 本周尚无测评报告；可先在指挥台点「立即测评」。")
    lines.extend(
        [
            "",
            "## 二、策略与人格调整",
            f"- 当前人格：{persona}。",
            f"- 启用提示词模块：{enabled_modules}。",
            "",
            "## 三、资产变化",
            f"- 新增工作流 {assets['workflows_created']} 个；"
            f"新增技能 {assets['skills_created']} 个；"
            f"失败场景更新 {assets['failures_seen']} 个。",
            "",
            "## 四、成本与效率",
            f"- 运行：{_delta_line(data['runs'], previous['runs'])}；"
            f"子任务成功率 {round(data['success_rate'] * 100)}%"
            f"（上期 {round(previous['success_rate'] * 100)}%）。",
            f"- Token：{_delta_line(data['tokens'], previous['tokens'])}；"
            f"模型费用 {cost_delta} 元。",
            f"- 平均耗时：{data['avg_latency_ms']} ms（上期 {previous['avg_latency_ms']} ms）。",
            "",
            "## 五、下周建议",
        ]
    )
    if data["error_codes"]:
        top = next(iter(data["error_codes"]))
        lines.append(f"- 优先处理高频错误 {top}；先查黑名单规避，再考虑换助手或补工具。")
    if data["success_rate"] < 0.7 and data["subtasks"]:
        lines.append("- 成功率偏低：缩小单任务范围，交付前增加一次校验步骤。")
    if data["llm_cost"] > previous["llm_cost"] and previous["llm_cost"] > 0:
        lines.append("- 成本上升：检查是否有重复规划或冗余上下文，必要时关闭非核心提示词模块。")
    if not data["error_codes"] and data["runs"]:
        lines.append("- 本周运行平稳：保持成熟工作流复用，谨慎开启新方案。")
    if not data["runs"]:
        lines.append("- 本周无运行：下周先跑一条真实业务线，避免画像与资产空转。")
    return {
        "week_key": current_week,
        "period": _period(start, end),
        "markdown": "\n".join(lines),
        "metrics": {
            **data,
            "previous": previous,
            "assets": assets,
            "persona": persona,
        },
        "evaluation": evaluator.evaluation_out(evaluation) if evaluation else None,
    }


def _month_metrics(db: Session, *, yard_id: str, start: datetime, end: datetime) -> dict[str, Any]:
    data = _aggregate(db, yard_id, start, end)
    assets = _asset_delta(db, yard_id, start, end)
    active_workflows = (
        db.query(CommanderWorkflow)
        .filter(
            CommanderWorkflow.yard_id == yard_id,
            CommanderWorkflow.status == WORKFLOW_ACTIVE,
        )
        .count()
    )
    active_skills = (
        db.query(CommanderSkillSnippet)
        .filter(
            CommanderSkillSnippet.yard_id == yard_id,
            CommanderSkillSnippet.status == SKILL_ACTIVE,
        )
        .count()
    )
    active_failures = (
        db.query(CommanderFailureCase)
        .filter(
            CommanderFailureCase.yard_id == yard_id,
            CommanderFailureCase.status == FAILURE_ACTIVE,
        )
        .count()
    )
    evaluations = list(
        db.scalars(
            select(CommanderEvaluation).where(
                CommanderEvaluation.yard_id == yard_id,
                CommanderEvaluation.created_at >= start,
                CommanderEvaluation.created_at < end,
            )
        )
    )
    totals = [evaluator.total_score(row.scores_json or {}) for row in evaluations]
    return {
        **data,
        "assets": assets,
        "active_workflows": active_workflows,
        "active_skills": active_skills,
        "active_failures": active_failures,
        "evaluations": len(evaluations),
        "avg_evaluation_score": round(sum(totals) / len(totals), 1) if totals else 0.0,
    }


def run_iteration(
    db: Session,
    *,
    ctx: YardContext,
    month: str | None = None,
    settings: dict[str, Any] | None = None,
) -> tuple[CommanderIteration, bool]:
    """生成一个月的迭代版本；同月幂等，返回 (row, created)。"""
    month_key = month or _previous_month()
    existing = db.scalar(
        select(CommanderIteration).where(
            CommanderIteration.yard_id == ctx.yard_id,
            CommanderIteration.month_key == month_key,
        )
    )
    if existing is not None:
        return existing, False
    start, end = _month_bounds(month_key)
    cfg = settings or commander_settings.settings_for(db, ctx.yard_id)
    metrics = _month_metrics(db, yard_id=ctx.yard_id, start=start, end=end)
    persona = str(cfg.get("persona") or "balanced")
    seq = db.query(CommanderIteration).filter(CommanderIteration.yard_id == ctx.yard_id).count() + 1
    version_label = f"V-{month_key.replace('-', '')}-{seq}"
    label = f"月度迭代 {version_label}"
    snapshot = snapshots.create_snapshot(
        db,
        yard_id=ctx.yard_id,
        label=label[:60],
        reason=f"{month_key} 月度迭代前快照"[:200],
        source="monthly",
        created_by=ctx.user_id,
        settings=cfg,
    )
    summary = (
        f"{month_key} 共 {metrics['runs']} 次运行，子任务成功率 "
        f"{round(metrics['success_rate'] * 100)}%；活跃工作流 "
        f"{metrics['active_workflows']} 个、技能 {metrics['active_skills']} 个；"
        f"人格 {persona}。"
    )
    lines = [
        f"# 月度迭代报告 {version_label}",
        "",
        f"月份：{month_key}",
        "",
        "## 一、版本变更说明",
        f"- 版本号：{version_label}",
        f"- 人格：{persona}",
        "- 已生成设置快照，可回滚到迭代前状态。",
        "",
        "## 二、全月指标",
        f"- 运行 {metrics['runs']} 次；子任务 {metrics['ok']}/{metrics['subtasks']} 成功，"
        f"成功率 {round(metrics['success_rate'] * 100)}%。",
        f"- Token {metrics['tokens']:,}；模型费用 ¥{metrics['llm_cost']:.4f}；"
        f"平均耗时 {metrics['avg_latency_ms']} ms。",
        f"- 测评 {metrics['evaluations']} 份，平均综合分 {metrics['avg_evaluation_score']}。",
        "",
        "## 三、资产与风险",
        f"- 活跃工作流 {metrics['active_workflows']} 个；活跃技能 {metrics['active_skills']} 个；"
        f"失败黑名单 {metrics['active_failures']} 条。",
        f"- 本月新增工作流 {metrics['assets']['workflows_created']} 个，"
        f"新增技能 {metrics['assets']['skills_created']} 个。",
    ]
    if metrics["error_codes"]:
        top = next(iter(metrics["error_codes"]))
        lines.append(f"- 长期风险：高频错误 {top}，下月优先修复或固化规避方案。")
    else:
        lines.append("- 长期风险：本月无高频错误，继续保持。")
    lines.extend(
        [
            "",
            "## 四、下月运行范式",
            "- 优先复用成熟工作流；新方案先在低风险任务试点，再纳入标准流程。",
            "- 保持失败黑名单定期清理：已解决的要标记，复发的自动重新启用。",
            "- 提示词只保留必要模块，成本上升时优先压缩上下文而不是减少交付校验。",
        ]
    )
    row = CommanderIteration(
        yard_id=ctx.yard_id,
        month_key=month_key,
        version_label=version_label,
        persona=persona,
        summary=summary,
        report_md="\n".join(lines),
        metrics_json=metrics,
        changes_json={
            "version_label": version_label,
            "persona": persona,
            "prompt_modules": list(cfg.get("prompt_modules") or []),
            "snapshot_id": snapshot.id,
        },
        snapshot_id=snapshot.id,
        created_by=ctx.user_id,
        finished_at=_now(),
    )
    # 同月并发首建（定时任务与手动触发撞在一起）会撞 (yard_id, month_key) 唯一约束：
    # 包在 SAVEPOINT 里，撞了就把对方已写入的那条当结果返回，不牵连本轮刚建的迭代快照。
    savepoint = db.begin_nested()
    try:
        db.add(row)
        db.flush()
    except IntegrityError:
        savepoint.rollback()
        raced = db.scalar(
            select(CommanderIteration).where(
                CommanderIteration.yard_id == ctx.yard_id,
                CommanderIteration.month_key == month_key,
            )
        )
        if raced is not None:
            return raced, False
        raise
    savepoint.commit()
    return row, True


def iteration_out(row: CommanderIteration) -> dict[str, Any]:
    return {
        "id": row.id,
        "month_key": row.month_key,
        "version_label": row.version_label,
        "persona": row.persona,
        "summary": row.summary,
        "report_md": row.report_md,
        "metrics": row.metrics_json or {},
        "changes": row.changes_json or {},
        "snapshot_id": row.snapshot_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def list_iterations(db: Session, yard_id: str, limit: int = 24) -> list[CommanderIteration]:
    return list(
        db.scalars(
            select(CommanderIteration)
            .where(CommanderIteration.yard_id == yard_id)
            .order_by(CommanderIteration.created_at.desc())
            .limit(max(1, min(limit, 60)))
        )
    )


def latest_iteration(db: Session, yard_id: str) -> CommanderIteration | None:
    return db.scalar(
        select(CommanderIteration)
        .where(CommanderIteration.yard_id == yard_id)
        .order_by(CommanderIteration.created_at.desc())
        .limit(1)
    )


__all__ = [
    "daily_report",
    "iteration_out",
    "latest_iteration",
    "list_iterations",
    "run_iteration",
    "weekly_optimization",
]
