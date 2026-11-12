"""用量口径、额度水位与升级引导（v3.80，审查 D-19/D-20/D-21）。

这一模块回答运营与用户最关心的三个问题，并且**只用一套数字**回答：

1. **我用了多少、还剩多少**（``usage_summary``）
   —— 口径与套餐卖的东西完全一致（tokens / calls / cost），
   与配额页的"可用预算"同源（审查 D-21）。
2. **还能用多久**（``usage_summary.anchor``）
   —— 用真实历史单次消耗均值换算"剩余额度≈还能问 N 次"，
   给价格一个可比的量纲锚点（审查 D-18）。
3. **该不该升档，升到哪一档**（``recommendation``）
   —— 按近 30 天真实消耗算覆盖率，给出具体套餐与理由，
   把"提额申请"从一张空白表变成一道选择题（审查 D-19/D-20）。

全部只读：写额度仍走 billing.service 的到账链路。
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.billing.catalog import environment_baseline, load_plans
from app.modules.billing.plans import METRIC_UNITS
from app.modules.llm.models import LLMCall
from app.modules.quota import engine as quota_engine
from app.modules.quota.models import METRIC_LABELS, QuotaPolicy

#: 升级观察窗口（天）：按近 30 天真实消耗推算
LOOKBACK_DAYS = 30
#: 目标余量倍数：推荐档位要能覆盖当前消耗的 1.5 倍
COVERAGE_TARGET = 1.5
#: 触发升级提醒的用量水位
UPGRADE_TRIGGER_PERCENT = 70.0
#: 单次消耗的兜底均值（历史不足时用，避免出现除零）
FALLBACK_TOKENS_PER_CALL = 3000.0


def _now() -> datetime:
    return datetime.now(UTC)


def _yard_policies(db: Session, yard_id: str) -> list[QuotaPolicy]:
    return list(
        db.scalars(
            select(QuotaPolicy).where(
                QuotaPolicy.yard_id == yard_id,
                QuotaPolicy.scope_type == "yard",
                QuotaPolicy.scope_key == "",
            )
        )
    )


def _recent_usage(db: Session, yard_id: str, days: int = LOOKBACK_DAYS) -> dict[str, Any]:
    """近 N 天的真实模型调用用量（来自 llm_calls，与成本核算同源）。

    聚合在数据库里完成（2026-11-13 二次深审）：重度小院 30 天可达十万级
    调用行，此前逐行拉到 Python 累加，付费页每次打开都要拖全量行；
    ``status='success'`` 过滤与求和都下沉 SQL，只回一行聚合结果。
    """
    since = _now() - timedelta(days=max(1, days))
    row = db.execute(
        select(
            func.coalesce(
                func.sum(
                    func.coalesce(LLMCall.input_tokens, 0.0)
                    + func.coalesce(LLMCall.output_tokens, 0.0)
                ),
                0.0,
            ).label("tokens"),
            func.coalesce(func.sum(LLMCall.estimated_cost), 0.0).label("cost"),
            func.count().label("calls"),
        ).where(
            LLMCall.yard_id == yard_id,
            LLMCall.created_at >= since,
            LLMCall.status == "success",
        )
    ).one()
    return {
        "tokens": float(row.tokens or 0),
        "calls": float(row.calls or 0),
        "cost": float(row.cost or 0),
        "days": days,
    }


def _budget(db: Session, yard_id: str) -> dict[str, dict[str, float]]:
    """当前预算：每个指标的上限、已用、临时额度余额。"""
    out: dict[str, dict[str, float]] = {}
    for policy in _yard_policies(db, yard_id):
        used, _start = quota_engine.current_usage(db, policy)
        limit = quota_engine.effective_limit(db, policy)
        grants = quota_engine.grant_total(db, policy.id)
        out[policy.metric] = {
            "limit": float(limit) if math.isfinite(limit) else -1.0,
            "used": float(used),
            "granted": float(grants),
            "remaining": float(max(limit - used, 0.0)) if math.isfinite(limit) else -1.0,
            "percent": round(float(used) / float(limit) * 100, 2)
            if math.isfinite(limit) and limit > 0
            else 0.0,
        }
    return out


def usage_summary(db: Session, yard_id: str) -> dict[str, Any]:
    """小院当前用量/额度/水位（与套餐卖的东西同一套指标）。"""
    budget = _budget(db, yard_id)
    recent = _recent_usage(db, yard_id)
    baseline = environment_baseline(db, yard_id)

    tokens_per_call = (
        recent["tokens"] / recent["calls"] if recent["calls"] > 0 else FALLBACK_TOKENS_PER_CALL
    )
    metrics: list[dict[str, Any]] = []
    for metric, label in METRIC_LABELS.items():
        info = budget.get(metric)
        limit = info["limit"] if info else -1.0
        used = info["used"] if info else 0.0
        remaining = info["remaining"] if info else -1.0
        metrics.append(
            {
                "metric": metric,
                "metric_label": label,
                "unit": METRIC_UNITS.get(metric, ("", ""))[1],
                "limit": None if limit < 0 else round(limit, 4),
                "unlimited": limit < 0,
                "used": round(used, 4),
                "granted": round(info["granted"], 4) if info else 0.0,
                "remaining": None if remaining < 0 else round(remaining, 4),
                "usage_percent": round(info["percent"], 2) if info else 0.0,
                "baseline": round(float(baseline.get(metric, 0.0)), 4),
            }
        )

    # 量纲锚点：剩余 Token 还能问多少次（按真实单次消耗均值）
    token_metric = next((m for m in metrics if m["metric"] == "tokens"), None)
    anchor: dict[str, Any] = {
        "tokens_per_call": round(tokens_per_call, 1),
        "based_on_calls": int(recent["calls"]),
        "calls_remaining": 0,
        "days_remaining": 0,
    }
    if token_metric and token_metric["remaining"] is not None:
        remaining = float(token_metric["remaining"])
        anchor["calls_remaining"] = int(remaining / tokens_per_call) if tokens_per_call > 0 else 0
        daily = recent["tokens"] / max(1, recent["days"])
        anchor["days_remaining"] = int(remaining / daily) if daily > 0 else 0

    top_percent = max((m["usage_percent"] for m in metrics), default=0.0)
    return {
        "window_days": LOOKBACK_DAYS,
        "recent": {
            "tokens": round(recent["tokens"], 2),
            "calls": int(recent["calls"]),
            "cost": round(recent["cost"], 4),
        },
        "metrics": metrics,
        "anchor": anchor,
        "max_usage_percent": round(top_percent, 2),
        "needs_attention": top_percent >= UPGRADE_TRIGGER_PERCENT,
    }


def recommendation(db: Session, yard_id: str) -> dict[str, Any]:
    """升级引导：按近 30 天真实消耗给出"该升到哪一档、为什么、覆盖几倍"。"""
    summary = usage_summary(db, yard_id)
    recent = summary["recent"]
    plans = [p for p in load_plans(db) if p.kind == "subscription"]
    plans.sort(key=lambda p: p.price_yuan)

    baseline = environment_baseline(db, yard_id)
    target_tokens = float(recent["tokens"]) * COVERAGE_TARGET
    target_calls = float(recent["calls"]) * COVERAGE_TARGET
    target_cost = float(recent["cost"]) * COVERAGE_TARGET

    chosen = None
    for plan in plans:
        tokens = float(baseline.get("tokens", 0.0))
        calls = float(baseline.get("calls", 0.0))
        cost = float(baseline.get("cost", 0.0))
        for item in plan.items:
            if item.metric == "tokens":
                tokens += item.amount
            elif item.metric == "calls":
                calls += item.amount
            elif item.metric == "cost":
                cost += item.amount
        covers = tokens >= target_tokens and calls >= target_calls and cost >= target_cost
        if covers:
            chosen = (plan, tokens, calls, cost)
            break

    current_limit = next((m["limit"] for m in summary["metrics"] if m["metric"] == "tokens"), None)
    coverage_ratio = 0.0
    if current_limit and current_limit > 0:
        coverage_ratio = round(float(recent["tokens"]) / float(current_limit), 3)

    if chosen is None:
        reason = (
            "近 30 天用量已经超过现有所有档位的覆盖范围，建议联系商务单独报价；"
            "也可以先用加油包应急。"
        )
        return {
            "recommended_code": None,
            "recommended_name": None,
            "reason": reason,
            "coverage_ratio": coverage_ratio,
            "target_multiple": COVERAGE_TARGET,
            "usage": summary["recent"],
            "upgrade_triggered": summary["needs_attention"],
            "suggestions": [
                {
                    "code": plan.code,
                    "name": plan.name,
                    "price_yuan": plan.price_yuan,
                    "tagline": plan.tagline,
                }
                for plan in plans
            ],
        }

    plan, tokens, calls, cost = chosen
    if recent["calls"] <= 0 or recent["tokens"] <= 0:
        reason = (
            f"近 {LOOKBACK_DAYS} 天还没有模型调用记录，当前额度足够使用。"
            f"若后续要做指挥官自动化或批量诊断，建议直接上 {plan.name}"
            f"（{int(tokens):,} Token/月，含 ¥{cost:,.0f} 模型算力额度）。"
        )
        covers_ratio = 0.0
    else:
        covers_ratio = round(tokens / float(recent["tokens"]), 2)
        reason = (
            f"近 {LOOKBACK_DAYS} 天实际消耗 {int(recent['tokens']):,} Token、"
            f"{int(recent['calls']):,} 次模型调用；{plan.name}可提供 {int(tokens):,} Token/月，"
            f"约为当前消耗的 {covers_ratio} 倍，并额外包含 ¥{cost:,.0f} 模型算力额度。"
        )
    return {
        "recommended_code": plan.code,
        "recommended_name": plan.name,
        "reason": reason,
        "coverage_ratio": coverage_ratio,
        "target_multiple": COVERAGE_TARGET,
        "usage": summary["recent"],
        "upgrade_triggered": summary["needs_attention"],
        "suggestions": [
            {
                "code": p.code,
                "name": p.name,
                "price_yuan": p.price_yuan,
                "tagline": p.tagline,
            }
            for p in plans
        ],
    }


__all__ = [
    "COVERAGE_TARGET",
    "LOOKBACK_DAYS",
    "UPGRADE_TRIGGER_PERCENT",
    "recommendation",
    "usage_summary",
]
