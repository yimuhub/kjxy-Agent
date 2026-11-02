"""独立监督测评 Agent（v3.44 P1）。

职责边界：
- 只读指挥官台账（``commander_runs`` / 子任务 / LLM 账本 / 记忆计数），不参与调度；
- 生成结构化周报并写入 ``commander_evaluations``；指挥官无权修改历史报告；
- 测评报告可建议人格，但是否落地由人格锁、进化锁与自动人格开关共同决定；
- 模型不可用时用确定性规则评分兜底，保证离线也能出报告。

评分维度（0–100）：
1. 人格倾向与风险自控 ``persona``；
2. 决策调度 ``scheduling``；
3. 成本管控 ``cost``；
4. 故障处理 ``recovery``；
5. 跨 Agent 学习 ``learning``。
"""

from __future__ import annotations

import json
import math
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.logging import get_logger
from app.modules.agents.models import CREATED_FROM_COMMANDER, YardAgent
from app.modules.audit.service import log_audit
from app.modules.commander import metrics, planner, snapshots
from app.modules.commander import settings as commander_settings
from app.modules.commander.governance_models import (
    EVAL_SOURCE_LLM,
    EVAL_SOURCE_RULE,
    PERSONA_AGGRESSIVE,
    PERSONA_BALANCED,
    PERSONA_STEADY,
    SNAPSHOT_PERSONA,
    VALID_PERSONAS,
    CommanderEvaluation,
)
from app.modules.commander.models import (
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_PARTIAL,
    SOURCE_GENERATED,
    SUB_ERROR,
    SUB_OK,
    SUB_SKIPPED,
    CommanderRun,
    CommanderSubtask,
)
from app.modules.llm.models import LLMCall
from app.modules.llm.schemas import ChatMessage
from app.modules.llm.service import complete_with_logging
from app.modules.memory.models import AgentMemory

log = get_logger(__name__)

PERSONA_LABELS = {
    PERSONA_AGGRESSIVE: "进取型",
    PERSONA_STEADY: "稳健保守型",
    PERSONA_BALANCED: "平衡型",
}
SCORE_LABELS = {
    "persona": "人格一致性",
    "scheduling": "决策调度",
    "cost": "成本管控",
    "recovery": "故障处理",
    "learning": "跨 Agent 学习",
}
#: 低于该运行数不自动切换人格，避免一周没跑就改画像
MIN_RUNS_FOR_PERSONA = 3


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, value)), 1)


def _rate(part: int, total: int) -> float:
    return part / total if total else 0.0


def period_bounds(now: datetime | None = None) -> tuple[datetime, datetime, str]:
    end = (now or datetime.now(UTC)).astimezone(UTC)
    start = end - timedelta(days=7)
    iso = end.isocalendar()
    return start, end, f"{iso.year}-W{iso.week:02d}"


def collect_metrics(db: Session, yard_id: str, start: datetime, end: datetime) -> dict[str, Any]:
    """汇总测评证据；全部为只读查询。"""
    rows = list(
        db.scalars(
            select(CommanderRun).where(
                CommanderRun.yard_id == yard_id,
                CommanderRun.created_at >= start,
                CommanderRun.created_at <= end,
            )
        )
    )
    runs = [run for run in rows if run.error_code != metrics.NEEDS_INPUT_CODE]
    run_ids = [run.id for run in runs]
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

    sub_counts = {SUB_OK: 0, SUB_ERROR: 0, SUB_SKIPPED: 0}
    for subtask in subtasks:
        if subtask.status in sub_counts:
            sub_counts[subtask.status] += 1
    sub_total = len(subtasks)
    extra_attempts = sum(max(0, int(sub.attempts or 0) - 1) for sub in subtasks)
    error_codes = Counter(
        str(sub.error_code) for sub in subtasks if sub.status == SUB_ERROR and sub.error_code
    )
    remediated = [sub for sub in subtasks if sub.remediation_json]
    remediated_ok = [sub for sub in remediated if sub.status == SUB_OK]
    generated = [sub for sub in subtasks if sub.agent_source == SOURCE_GENERATED]
    artifacts = sum(len(sub.artifact_json or []) for sub in subtasks)

    fallback_runs = sum(1 for run in runs if bool((run.plan_json or {}).get("fallback")))
    degraded_runs = sum(1 for run in runs if bool(run.degraded))
    budget_warning = sum(1 for run in runs if run.budget_status == "warning")
    budget_exceeded = sum(1 for run in runs if run.budget_status == "exceeded")
    tokens = sum(int(run.tokens_total or 0) for run in runs)
    latencies = [
        int(run.latency_ms or 0)
        for run in runs
        if run.status in (RUN_COMPLETED, RUN_PARTIAL, RUN_FAILED)
    ]

    generated_agents = db.scalar(
        select(func.count())
        .select_from(YardAgent)
        .where(
            YardAgent.yard_id == yard_id,
            YardAgent.created_from == CREATED_FROM_COMMANDER,
            YardAgent.created_at >= start,
            YardAgent.created_at <= end,
        )
    )

    llm_row = db.execute(
        select(
            func.coalesce(func.count(), 0),
            func.coalesce(func.sum(LLMCall.input_tokens), 0),
            func.coalesce(func.sum(LLMCall.output_tokens), 0),
            func.coalesce(func.sum(LLMCall.estimated_cost), 0.0),
        ).where(
            LLMCall.yard_id == yard_id,
            LLMCall.created_at >= start,
            LLMCall.created_at <= end,
        )
    ).one()

    memory_rows = db.execute(
        select(AgentMemory.kind, func.count())
        .where(
            AgentMemory.yard_id == yard_id,
            AgentMemory.created_at >= start,
            AgentMemory.created_at <= end,
        )
        .group_by(AgentMemory.kind)
    ).all()
    memory_counts = {str(kind): int(count) for kind, count in memory_rows}

    return {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "runs": {
            "total": len(runs),
            "completed": sum(1 for run in runs if run.status == RUN_COMPLETED),
            "partial": sum(1 for run in runs if run.status == RUN_PARTIAL),
            "failed": sum(1 for run in runs if run.status == RUN_FAILED),
            "fallback": fallback_runs,
            "degraded": degraded_runs,
            "budget_warning": budget_warning,
            "budget_exceeded": budget_exceeded,
            "tokens": tokens,
        },
        "subtasks": {
            "total": sub_total,
            "ok": sub_counts[SUB_OK],
            "error": sub_counts[SUB_ERROR],
            "skipped": sub_counts[SUB_SKIPPED],
            "extra_attempts": extra_attempts,
            "remediated": len(remediated),
            "remediated_ok": len(remediated_ok),
            "generated_agents_used": len(generated),
            "artifacts": artifacts,
        },
        "errors": {
            "top_codes": [
                {"code": code, "count": count} for code, count in error_codes.most_common(5)
            ],
            "distinct": len(error_codes),
        },
        "cost": {
            "calls": int(llm_row[0] or 0),
            "input_tokens": int(llm_row[1] or 0),
            "output_tokens": int(llm_row[2] or 0),
            "estimated": round(float(llm_row[3] or 0), 4),
        },
        "memory": {
            "episodes": memory_counts.get("episode", 0),
            "summaries": memory_counts.get("summary", 0),
        },
        "auto_created_agents": int(generated_agents or 0),
        "latency": {
            "p90": _percentile(latencies, 90),
            "max": max(latencies) if latencies else 0,
        },
    }


def _percentile(values: list[int], percentile: int) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile / 100 * len(ordered)) - 1)
    return int(ordered[index])


def classify_persona(metrics_payload: dict[str, Any]) -> tuple[str, float, dict[str, float]]:
    """按行为证据判人格；返回 (persona, risk 0–1, 风险构成)。"""
    runs = metrics_payload["runs"]
    subs = metrics_payload["subtasks"]
    total_runs = int(runs["total"])
    total_subs = int(subs["total"])
    retry_rate = _rate(int(subs["extra_attempts"]), total_subs)
    generated_rate = _rate(int(subs["generated_agents_used"]), total_subs)
    remediation_rate = _rate(int(subs["remediated"]), total_subs)
    fallback_rate = _rate(int(runs["fallback"]), total_runs)
    degraded_rate = _rate(int(runs["degraded"]), total_runs)
    risk = (
        0.30 * min(1.0, retry_rate)
        + 0.30 * min(1.0, generated_rate)
        + 0.20 * min(1.0, remediation_rate)
        + 0.20 * (1.0 - min(1.0, fallback_rate))
        + 0.10 * min(1.0, degraded_rate)
    )
    risk = max(0.0, min(1.0, risk))
    if risk >= 0.45:
        persona = PERSONA_AGGRESSIVE
    elif risk <= 0.2:
        persona = PERSONA_STEADY
    else:
        persona = PERSONA_BALANCED
    parts = {
        "retry_rate": round(retry_rate, 4),
        "generated_agent_rate": round(generated_rate, 4),
        "remediation_rate": round(remediation_rate, 4),
        "fallback_rate": round(fallback_rate, 4),
        "degraded_rate": round(degraded_rate, 4),
        "risk": round(risk, 4),
    }
    return persona, risk, parts


def rule_scores(metrics_payload: dict[str, Any], persona: str, risk: float) -> dict[str, float]:
    runs = metrics_payload["runs"]
    subs = metrics_payload["subtasks"]
    total_runs = int(runs["total"])
    total_subs = int(subs["total"])
    if total_runs == 0:
        neutral = 60.0
        return {
            "persona": neutral,
            "scheduling": neutral,
            "cost": neutral,
            "recovery": neutral,
            "learning": neutral,
        }

    sub_success = _rate(int(subs["ok"]), total_subs)
    plan_success = 1.0 - _rate(int(runs["fallback"]), total_runs)
    error_rate = _rate(int(subs["error"]), total_subs)
    retry_rate = _rate(int(subs["extra_attempts"]), total_subs)
    remediation_ok = _rate(int(subs["remediated_ok"]), max(1, int(subs["remediated"])))
    warned = _rate(int(runs["budget_warning"]), total_runs)
    exceeded = _rate(int(runs["budget_exceeded"]), total_runs)

    persona_score = _clamp(55 + 45 * min(1.0, abs(risk - 0.325) / 0.325))
    scheduling = _clamp(100 * (0.5 * sub_success + 0.3 * plan_success + 0.2 * (1 - warned)))
    cost = _clamp(100 - 50 * exceeded - 25 * warned)
    recovery = _clamp(100 - 60 * error_rate - 20 * retry_rate + 10 * remediation_ok)
    memory = metrics_payload["memory"]
    learning = _clamp(
        50
        + 15 * min(1.0, int(memory["summaries"]) / 2)
        + 15 * min(1.0, int(memory["episodes"]) / 5)
        + 10 * min(1.0, int(subs["generated_agents_used"]) / 3)
        + 10 * min(1.0, int(subs["artifacts"]) / 5)
    )
    return {
        "persona": persona_score,
        "scheduling": scheduling,
        "cost": cost,
        "recovery": recovery,
        "learning": learning,
    }


def total_score(scores: dict[str, Any]) -> float:
    weights = {
        "persona": 0.15,
        "scheduling": 0.25,
        "cost": 0.20,
        "recovery": 0.20,
        "learning": 0.20,
    }
    return _clamp(sum(float(scores.get(key, 0)) * weight for key, weight in weights.items()))


def _rule_findings(
    metrics_payload: dict[str, Any], scores: dict[str, float], persona: str
) -> tuple[str, list[str], list[str], list[str], str]:
    runs = metrics_payload["runs"]
    subs = metrics_payload["subtasks"]
    label = PERSONA_LABELS[persona]
    summary = (
        f"本周期运行 {runs['total']} 次，子任务成功率 "
        f"{round(_rate(int(subs['ok']), int(subs['total'])) * 100)}%，"
        f"判定为{label}。"
    )
    strengths: list[str] = []
    shortfalls: list[str] = []
    recommendations: list[str] = []
    if int(subs["ok"]) and _rate(int(subs["ok"]), int(subs["total"])) >= 0.8:
        strengths.append("子任务成功率保持在 80% 以上，交付链路稳定。")
    else:
        shortfalls.append("子任务成功率低于 80%，需要检查失败集中在哪些任务类型。")
        recommendations.append("优先复盘失败子任务的错误码，补一条对应工作流或前置校验。")
    if int(subs["extra_attempts"]) == 0:
        strengths.append("本周期没有额外重试，首次执行质量较好。")
    else:
        shortfalls.append(f"额外重试 {subs['extra_attempts']} 次，存在指令或选兵不匹配。")
        recommendations.append("把高频重试场景沉淀成标准工作流，减少重复试错。")
    if int(runs["budget_exceeded"]) or int(runs["budget_warning"]):
        shortfalls.append(
            f"预算告警 {runs['budget_warning']} 次、超限 {runs['budget_exceeded']} 次。"
        )
        recommendations.append("压缩规划上下文，优先复用记忆摘要，降低单轮 token 基线。")
    else:
        strengths.append("本周期没有触发预算告警，成本控制正常。")
    if int(metrics_payload["memory"]["summaries"]) or int(metrics_payload["memory"]["episodes"]):
        strengths.append("运行复盘与日摘要已持续沉淀，可用于后续同类任务匹配。")
    else:
        recommendations.append("让每次运行都产生复盘，至少保证日摘要不断档。")
    if metrics_payload["errors"]["top_codes"]:
        top = metrics_payload["errors"]["top_codes"][0]
        shortfalls.append(f"最高频错误码 {top['code']} 出现 {top['count']} 次。")
    return summary, strengths, shortfalls, recommendations, label


def build_report(
    *,
    week_key: str,
    persona: str,
    scores: dict[str, float],
    summary: str,
    strengths: list[str],
    shortfalls: list[str],
    recommendations: list[str],
    metrics_payload: dict[str, Any],
) -> str:
    total = total_score(scores)
    lines = [
        f"# 指挥官周度测评报告（{week_key}）",
        "",
        "## 一、人格判定",
        f"- 人格标签：**{PERSONA_LABELS[persona]}**（{persona}）",
        f"- 综合得分：**{total}** / 100",
        "",
        "## 二、分项得分",
        "| 维度 | 得分 |",
        "| --- | --- |",
    ]
    for key, label in SCORE_LABELS.items():
        lines.append(f"| {label} | {scores.get(key, 0)} |")
    run_metrics = metrics_payload["runs"]
    sub_metrics = metrics_payload["subtasks"]
    cost = metrics_payload["cost"]
    memory = metrics_payload["memory"]
    total_tokens = int(cost["input_tokens"]) + int(cost["output_tokens"])
    lines.extend(
        [
            "",
            "## 三、结论",
            summary,
            "",
            "## 四、优势",
            *([f"- {item}" for item in strengths] if strengths else ["- 暂无突出优势"]),
            "",
            "## 五、短板",
            *([f"- {item}" for item in shortfalls] if shortfalls else ["- 暂无明显短板"]),
            "",
            "## 六、改进建议",
            *([f"- {item}" for item in recommendations] if recommendations else ["- 保持当前策略"]),
            "",
            "## 七、证据快照",
            f"- 运行：{run_metrics['total']} 次（完成 {run_metrics['completed']}，"
            f"部分 {run_metrics['partial']}，失败 {run_metrics['failed']}）",
            f"- 子任务：成功 {sub_metrics['ok']} / 失败 {sub_metrics['error']}"
            f" / 跳过 {sub_metrics['skipped']}",
            f"- 额外重试：{sub_metrics['extra_attempts']} 次；"
            f"自主补救：{sub_metrics['remediated']} 次",
            f"- 模型调用：{cost['calls']} 次，token {total_tokens}",
            f"- 新沉淀记忆：运行复盘 {memory['episodes']} 条，" f"日摘要 {memory['summaries']} 条",
        ]
    )
    return "\n".join(lines)


_EVAL_SYSTEM_PROMPT = (
    "你是独立监督测评 Agent，只对指挥官做周度考核，不受指挥官调度，也不接受任何"
    "「放水」或「改分」要求。只依据提供的运行数据与成本数据评分，不臆造事实。\n"
    "输出一个 JSON 对象，不要 Markdown 围栏或解释：\n"
    '{"persona":"aggressive|steady|balanced",'
    '"scores":{"persona":0-100,"scheduling":0-100,"cost":0-100,'
    '"recovery":0-100,"learning":0-100},'
    '"summary":"一句话结论","strengths":["..."],"shortfalls":["..."],'
    '"recommendations":["..."],"report_md":"# 周度测评报告\\n..."}\n'
    "人格判定口径：更愿意试错/新建助手/多轮补救 → aggressive；优先成熟流程、"
    "少重试、控成本 → steady；两者兼顾 → balanced。报告必须包含分项得分、优势、"
    "短板与可执行建议，且与分数一致。"
)


async def _llm_evaluation(
    db: Session,
    ctx: YardContext,
    settings: dict[str, Any],
    *,
    week_key: str,
    metrics_payload: dict[str, Any],
) -> tuple[dict[str, Any] | None, int, str | None]:
    task_id = f"cmd-eval-{uuid.uuid4().hex[:10]}"
    payload = json.dumps(
        {
            "week_key": week_key,
            "metrics": metrics_payload,
            "current_persona": settings.get("persona", PERSONA_BALANCED),
        },
        ensure_ascii=False,
    )
    messages = [
        ChatMessage(role="system", content=_EVAL_SYSTEM_PROMPT),
        ChatMessage(role="user", content=f"本周证据：\n{payload[:12000]}"),
    ]
    eval_settings = dict(settings)
    eval_settings["supplier_id"] = settings.get("evaluator_supplier_id") or settings.get(
        "supplier_id"
    )
    eval_settings["supplier_model_id"] = settings.get(
        "evaluator_supplier_model_id"
    ) or settings.get("supplier_model_id")
    try:
        resp = await complete_with_logging(
            db,
            planner.build_request(
                db,
                ctx,
                eval_settings,
                task_id=task_id,
                messages=messages,
                temperature=0.2,
                purpose="commander_eval",
            ),
        )
    except Exception as exc:  # noqa: BLE001 - 测评失败必须退回规则评分
        log.warning("commander evaluation llm failed (rule fallback): %s", exc)
        return None, 0, None
    tokens = int(resp.input_tokens or 0) + int(resp.output_tokens or 0)
    data = planner.parse_json_object(resp.content or "")
    if not isinstance(data, dict):
        log.warning("commander evaluation llm returned non-json")
        return None, tokens, resp.model
    return data, tokens, resp.model


def _sanitize_llm(data: dict[str, Any], fallback_scores: dict[str, float]) -> dict[str, Any]:
    persona = str(data.get("persona") or "").strip()
    if persona not in VALID_PERSONAS:
        persona = PERSONA_BALANCED
    candidate = data.get("scores")
    raw_scores: dict[str, Any] = candidate if isinstance(candidate, dict) else {}
    scores: dict[str, float] = {}
    for key in SCORE_LABELS:
        raw_value = raw_scores.get(key)
        try:
            value = float(raw_value) if raw_value is not None else fallback_scores[key]
        except (TypeError, ValueError):
            value = fallback_scores[key]
        scores[key] = _clamp(value)
    strengths = [str(item)[:300] for item in (data.get("strengths") or []) if str(item).strip()]
    shortfalls = [str(item)[:300] for item in (data.get("shortfalls") or []) if str(item).strip()]
    recommendations = [
        str(item)[:300] for item in (data.get("recommendations") or []) if str(item).strip()
    ]
    report_md = str(data.get("report_md") or "").strip()
    summary = str(data.get("summary") or "").strip()
    return {
        "persona": persona,
        "scores": scores,
        "strengths": strengths[:8],
        "shortfalls": shortfalls[:8],
        "recommendations": recommendations[:8],
        "report_md": report_md,
        "summary": summary,
    }


def _apply_persona(
    db: Session,
    *,
    ctx: YardContext,
    settings: dict[str, Any],
    persona: str,
    week_key: str,
    evaluation: CommanderEvaluation,
) -> tuple[bool, str]:
    previous = str(settings.get("persona") or PERSONA_BALANCED)
    if persona == previous:
        return False, "same"
    if settings.get("persona_locked"):
        return False, "persona_locked"
    if settings.get("evolution_lock"):
        return False, "evolution_locked"
    if not settings.get("evaluator_auto_persona", True):
        return False, "auto_persona_off"
    if int((evaluation.metrics_json or {}).get("runs", {}).get("total", 0)) < MIN_RUNS_FOR_PERSONA:
        return False, "insufficient_samples"

    snapshots.create_snapshot(
        db,
        yard_id=ctx.yard_id,
        label=f"人格自动调整前快照 · {week_key}",
        reason=f"测评建议 {previous} → {persona}",
        source=SNAPSHOT_PERSONA,
        created_by=None,
        settings=settings,
    )
    commander_settings.update_settings(
        db,
        yard_id=ctx.yard_id,
        payload={
            **settings,
            "persona": persona,
        },
        actor_id=ctx.user_id,
        request_id=ctx.request_id,
    )
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="commander_persona_changed",
        resource_type="commander_evaluation",
        resource_id=evaluation.id,
        result="success",
        request_id=ctx.request_id,
        metadata={
            "from": previous,
            "to": persona,
            "week_key": week_key,
            "source": "evaluation",
        },
    )
    return True, "applied"


async def run_evaluation(
    db: Session,
    ctx: YardContext,
    *,
    use_llm: bool = True,
    force: bool = False,
    now: datetime | None = None,
) -> tuple[CommanderEvaluation, bool]:
    """生成或刷新本周测评；返回 (报告, 是否新建)。"""
    settings = commander_settings.settings_for(db, ctx.yard_id)
    start, end, week_key = period_bounds(now)
    existing = db.scalar(
        select(CommanderEvaluation).where(
            CommanderEvaluation.yard_id == ctx.yard_id,
            CommanderEvaluation.week_key == week_key,
        )
    )
    if existing is not None and not force:
        return existing, False

    metrics_payload = collect_metrics(db, ctx.yard_id, start, end)
    persona, risk, risk_parts = classify_persona(metrics_payload)
    metrics_payload["persona_risk"] = risk_parts
    scores = rule_scores(metrics_payload, persona, risk)
    summary, strengths, shortfalls, recommendations, _ = _rule_findings(
        metrics_payload, scores, persona
    )
    source = EVAL_SOURCE_RULE
    model: str | None = None
    tokens = 0

    if use_llm and settings.get("evaluator_enabled", True):
        data, tokens, model = await _llm_evaluation(
            db, ctx, settings, week_key=week_key, metrics_payload=metrics_payload
        )
        if data is not None:
            sanitized = _sanitize_llm(data, scores)
            persona = sanitized["persona"]
            scores = sanitized["scores"]
            summary = sanitized["summary"] or summary
            strengths = sanitized["strengths"] or strengths
            shortfalls = sanitized["shortfalls"] or shortfalls
            recommendations = sanitized["recommendations"] or recommendations
            report_md = sanitized["report_md"] or build_report(
                week_key=week_key,
                persona=persona,
                scores=scores,
                summary=summary,
                strengths=strengths,
                shortfalls=shortfalls,
                recommendations=recommendations,
                metrics_payload=metrics_payload,
            )
            source = EVAL_SOURCE_LLM
        else:
            report_md = ""
    else:
        report_md = ""

    if not report_md:
        report_md = build_report(
            week_key=week_key,
            persona=persona,
            scores=scores,
            summary=summary,
            strengths=strengths,
            shortfalls=shortfalls,
            recommendations=recommendations,
            metrics_payload=metrics_payload,
        )

    previous_persona = str(settings.get("persona") or PERSONA_BALANCED)
    if existing is None:
        evaluation = CommanderEvaluation(
            yard_id=ctx.yard_id,
            week_key=week_key,
            period_start=start,
            period_end=end,
            persona=persona,
            previous_persona=previous_persona,
            persona_applied=False,
            source=source,
            model=model,
            tokens=tokens,
            scores_json=scores,
            summary=summary,
            report_md=report_md,
            strengths_json=strengths,
            shortfalls_json=shortfalls,
            recommendations_json=recommendations,
            metrics_json=metrics_payload,
            created_by=ctx.user_id,
        )
        db.add(evaluation)
        db.flush()
        created = True
    else:
        evaluation = existing
        evaluation.period_start = start
        evaluation.period_end = end
        evaluation.persona = persona
        evaluation.previous_persona = previous_persona
        evaluation.source = source
        evaluation.model = model
        evaluation.tokens = tokens
        evaluation.scores_json = scores
        evaluation.summary = summary
        evaluation.report_md = report_md
        evaluation.strengths_json = strengths
        evaluation.shortfalls_json = shortfalls
        evaluation.recommendations_json = recommendations
        evaluation.metrics_json = metrics_payload
        evaluation.created_by = ctx.user_id
        db.add(evaluation)
        db.flush()
        created = False

    applied, reason = _apply_persona(
        db,
        ctx=ctx,
        settings=settings,
        persona=persona,
        week_key=week_key,
        evaluation=evaluation,
    )
    evaluation.persona_applied = applied
    evaluation.metrics_json = {**evaluation.metrics_json, "persona_reason": reason}
    db.add(evaluation)
    db.flush()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="commander_evaluation_created" if created else "commander_evaluation_refreshed",
        resource_type="commander_evaluation",
        resource_id=evaluation.id,
        result="success",
        request_id=ctx.request_id,
        metadata={
            "week_key": week_key,
            "persona": persona,
            "persona_applied": applied,
            "persona_reason": reason,
            "source": source,
        },
    )
    log.info(
        "commander evaluation yard=%s week=%s persona=%s applied=%s reason=%s source=%s",
        ctx.yard_id,
        week_key,
        persona,
        applied,
        reason,
        source,
    )
    return evaluation, created


def evaluation_out(row: CommanderEvaluation) -> dict[str, Any]:
    return {
        "id": row.id,
        "week_key": row.week_key,
        "period_start": row.period_start.isoformat() if row.period_start else None,
        "period_end": row.period_end.isoformat() if row.period_end else None,
        "persona": row.persona,
        "persona_label": PERSONA_LABELS.get(row.persona, row.persona),
        "previous_persona": row.previous_persona,
        "persona_applied": bool(row.persona_applied),
        "persona_reason": (row.metrics_json or {}).get("persona_reason"),
        "source": row.source,
        "model": row.model,
        "tokens": int(row.tokens or 0),
        "scores": row.scores_json or {},
        "total": total_score(row.scores_json or {}),
        "summary": row.summary,
        "report_md": row.report_md,
        "strengths": row.strengths_json or [],
        "shortfalls": row.shortfalls_json or [],
        "recommendations": row.recommendations_json or [],
        "metrics": row.metrics_json or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def latest_evaluation(db: Session, yard_id: str) -> CommanderEvaluation | None:
    return db.scalar(
        select(CommanderEvaluation)
        .where(CommanderEvaluation.yard_id == yard_id)
        .order_by(CommanderEvaluation.created_at.desc())
        .limit(1)
    )


def list_evaluations(db: Session, yard_id: str, limit: int = 12) -> list[CommanderEvaluation]:
    capped = max(1, min(int(limit or 12), 52))
    return list(
        db.scalars(
            select(CommanderEvaluation)
            .where(CommanderEvaluation.yard_id == yard_id)
            .order_by(CommanderEvaluation.created_at.desc())
            .limit(capped)
        )
    )


__all__ = [
    "PERSONA_LABELS",
    "SCORE_LABELS",
    "build_report",
    "classify_persona",
    "collect_metrics",
    "evaluation_out",
    "latest_evaluation",
    "list_evaluations",
    "period_bounds",
    "rule_scores",
    "run_evaluation",
    "total_score",
]
