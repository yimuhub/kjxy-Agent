"""套餐目录读写（v3.80，审查 D-18/D-21）。

**目录以数据库为准**：``billing_plans`` 表是运营可改的真相源（价格、卖点、
额度项、排序、上下架），改价不用发版；``plans.PLANS`` 只是种子值与空表兜底。

同时统一"卖什么"与"限什么"的口径（审查 D-21）：
- 额度指标只有 tokens / calls / cost 三个，与 ``quota.METRIC_LABELS`` 完全一致；
- 付费页展示的**总额度**（环境基础额度 + 套餐追加额度）由服务端算好后下发，
  与配额页的"可用预算"是同一套数字，运营报出"含多少 Token"有确定答案。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.billing.models import BillingPlan
from app.modules.billing.plans import (
    KIND_BOOSTER,
    METRIC_UNITS,
    PLANS,
    Plan,
    PlanItem,
)
from app.modules.quota import environments
from app.modules.quota.models import METRIC_LABELS

#: 允许的额度指标（与 quota 模块一致）
ALLOWED_METRICS = frozenset(METRIC_LABELS.keys())


def _items_from_json(raw: Any) -> tuple[PlanItem, ...]:
    """把库里存的 JSON 额度项还原成 PlanItem；非法项跳过而不是整条报废。"""
    if not isinstance(raw, list):
        return ()
    items: list[PlanItem] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        metric = str(entry.get("metric") or "").strip()
        if metric not in ALLOWED_METRICS:
            continue
        try:
            amount = float(entry.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if amount <= 0:
            continue
        window = str(entry.get("window") or "month").strip()
        try:
            valid_days = int(entry.get("valid_days") or 0)
        except (TypeError, ValueError):
            valid_days = 0
        items.append(
            PlanItem(
                metric=metric,
                window=window if window in ("day", "month") else "month",
                amount=amount,
                valid_days=max(0, valid_days),
                note=str(entry.get("note") or ""),
            )
        )
    return tuple(items)


def _highlights_of(raw: Any) -> tuple[str, ...]:
    """解析卖点：库里存 JSON 字符串数组，兼容直接存列表。"""
    if isinstance(raw, list):
        return tuple(str(x).strip() for x in raw if str(x).strip())
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return ()
        if isinstance(parsed, list):
            return tuple(str(x).strip() for x in parsed if str(x).strip())
    return ()


def _plan_from_row(row: BillingPlan) -> Plan:
    return Plan(
        code=row.code,
        name=row.name,
        kind=row.kind,
        price_yuan=float(row.price_yuan or 0),
        tagline=row.tagline or "",
        items=_items_from_json(row.items),
        valid_days=0,
        highlights=_highlights_of(getattr(row, "highlights", None)),
        active=bool(row.active),
        sort_order=int(row.sort_order or 0),
    )


def seed_plans_if_empty(db: Session) -> int:
    """表为空时把代码内置目录播种进去（老部署升级后的兜底）。"""
    existing = db.scalar(select(BillingPlan.id).limit(1))
    if existing:
        return 0
    created = 0
    for index, plan in enumerate(PLANS):
        db.add(
            BillingPlan(
                code=plan.code,
                name=plan.name,
                kind=plan.kind,
                price_yuan=plan.price_yuan,
                tagline=plan.tagline,
                items=[
                    {
                        "metric": item.metric,
                        "window": item.window,
                        "amount": item.amount,
                        "valid_days": item.valid_days,
                        "note": item.note,
                    }
                    for item in plan.items
                ],
                highlights=json.dumps(list(plan.highlights), ensure_ascii=False)
                if plan.highlights
                else None,
                note=None,
                sort_order=plan.sort_order or (index + 1) * 10,
                active=plan.active,
            )
        )
        created += 1
    db.commit()
    return created


def load_plans(db: Session, *, include_inactive: bool = False) -> list[Plan]:
    """读套餐目录：优先数据库，表为空时回落到代码内置值。"""
    try:
        rows = list(
            db.scalars(select(BillingPlan).order_by(BillingPlan.sort_order, BillingPlan.code))
        )
    except Exception:  # noqa: BLE001 - 表还没建（迁移未跑）时回落
        rows = []
    if not rows:
        return list(PLANS)
    if not include_inactive:
        rows = [row for row in rows if row.active]
    if not rows:
        return []
    return [_plan_from_row(row) for row in rows]


def get_plan_by_code(db: Session, code: str, *, include_inactive: bool = False) -> Plan | None:
    code = (code or "").strip()
    if not code:
        return None
    for plan in load_plans(db, include_inactive=include_inactive):
        if plan.code == code:
            return plan
    return None


def environment_baseline(db: Session, yard_id: str) -> dict[str, float]:
    """小院当前额度环境的**基础额度**（不含任何购买追加）。

    这是"套餐追加"之外的底座：付费页展示总额度时必须把它算进去，
    否则卖 200 万 Token 实际到账 220 万 Token，账就永远算不清（审查 D-21）。
    """
    from app.modules.quota.service import SETTINGS_KEY
    from app.modules.yards.settings_service import get_setting

    try:
        settings = get_setting(db, yard_id, SETTINGS_KEY)
    except Exception:  # noqa: BLE001 - 没有设置行时按默认环境算
        settings = {}
    environment = str((settings or {}).get("environment") or environments.DEFAULT_ENVIRONMENT)
    baseline: dict[str, float] = {}
    for policy in environments.template_policies(environment):
        metric = str(policy.get("metric") or "")
        if metric in ALLOWED_METRICS:
            baseline[metric] = baseline.get(metric, 0.0) + float(policy.get("limit_value") or 0)
    return baseline


def plan_payload(
    plan: Plan,
    *,
    baseline: dict[str, float] | None = None,
    include_inactive: bool = False,
) -> dict[str, Any]:
    """套餐对外载荷。

    ``items``        —— 本套餐追加的额度（到账时写进 quota_grants 的量）；
    ``entitlement``  —— 总额度 = 环境基础额度 + 追加额度（订阅档），或
    加油包这类一次性档**只列本次买到的量**（不把环境基础额度算进去，
    否则"¥299 加油包"会被读成"我一共才有 300 万 Token"，与卖点冲突）；
    ``highlights``   —— 可对比的卖点条目（运营可在后台改）。
    """
    base = baseline or {}
    is_booster = plan.kind == KIND_BOOSTER
    items = [
        {
            "metric": item.metric,
            "metric_label": METRIC_LABELS.get(item.metric, item.metric),
            "unit": item.unit,
            "window": item.window,
            "amount": item.amount,
            "note": item.note,
        }
        for item in plan.items
    ]
    entitlement: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in plan.items:
        seen.add(item.metric)
        item_base = 0.0 if is_booster else float(base.get(item.metric, 0.0))
        entitlement.append(
            {
                "metric": item.metric,
                "metric_label": METRIC_LABELS.get(item.metric, item.metric),
                "unit": item.unit,
                "window": item.window,
                "base": round(item_base, 4),
                "total": round(item_base + item.amount, 4),
                "note": item.note,
            }
        )
    # 套餐没覆盖的指标也展示出来（否则"含多少 Token"对不上一份完整账）
    if not is_booster:
        for metric, amount in base.items():
            if metric in seen:
                continue
            entitlement.append(
                {
                    "metric": metric,
                    "metric_label": METRIC_LABELS.get(metric, metric),
                    "unit": METRIC_UNITS.get(metric, ("", ""))[1],
                    "window": "month",
                    "base": round(float(amount), 4),
                    "total": round(float(amount), 4),
                    "note": "所选额度环境的基础额度（本套餐不额外追加）",
                }
            )
    return {
        "code": plan.code,
        "name": plan.name,
        "kind": plan.kind,
        "price_yuan": plan.price_yuan,
        "tagline": plan.tagline,
        "highlights": list(plan.highlights),
        "items": items,
        "entitlement": entitlement,
        "valid_days": plan.valid_days,
        "one_off": is_booster,
        # 运营后台要改「上下架 / 排序」，这两个字段随载荷一起下发，
        # 免得前端为了显示一个下拉框再发一次请求。
        "active": bool(plan.active),
        "sort_order": int(plan.sort_order),
    }


def list_plan_payloads(db: Session, yard_id: str) -> list[dict[str, Any]]:
    """付费页/套餐卡的完整载荷（含按本小院环境算出的总额度）。"""
    baseline = environment_baseline(db, yard_id)
    return [plan_payload(plan, baseline=baseline) for plan in load_plans(db)]


def update_plan(
    db: Session,
    *,
    code: str,
    actor_id: str,
    price_yuan: float | None = None,
    tagline: str | None = None,
    highlights: list[str] | None = None,
    items: list[dict[str, Any]] | None = None,
    sort_order: int | None = None,
    active: bool | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """运营改套餐（价格/卖点/额度项/排序/上下架）。改价不用发版。"""
    row = db.scalar(select(BillingPlan).where(BillingPlan.code == code))
    if row is None:
        # 表为空的老部署：先播种再改
        seed_plans_if_empty(db)
        row = db.scalar(select(BillingPlan).where(BillingPlan.code == code))
    if row is None:
        raise AppError("BILLING_PLAN_NOT_FOUND", "套餐不存在。", http_status=404)

    if price_yuan is not None:
        # 套餐是付费 SKU：0/负价会让沙箱、回单、回调三条支付链路零成本到账
        # （订单创建已按 price>0 拦截，这里从源头挡住 0 价上架）。
        # 免费发放走平台管理员发兑换码，不走 0 元套餐。
        if float(price_yuan) <= 0:
            raise AppError(
                "BILLING_PLAN_PRICE_INVALID",
                "套餐价格必须大于 0（免费发放请走兑换码，不要上架 0 元套餐）。",
                http_status=400,
            )
        row.price_yuan = float(price_yuan)
    if tagline is not None:
        row.tagline = tagline.strip()[:200]
    if highlights is not None:
        cleaned = [str(x).strip()[:80] for x in highlights if str(x).strip()][:8]
        row.highlights = json.dumps(cleaned, ensure_ascii=False) if cleaned else None
    if items is not None:
        cleaned_items: list[dict[str, Any]] = []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            metric = str(entry.get("metric") or "").strip()
            if metric not in ALLOWED_METRICS:
                raise AppError(
                    "INVALID_REQUEST",
                    f"额度指标 {metric!r} 不合法，只支持 {'/'.join(sorted(ALLOWED_METRICS))}。",
                    http_status=400,
                )
            try:
                amount = float(entry.get("amount") or 0)
            except (TypeError, ValueError) as e:
                raise AppError("INVALID_REQUEST", "额度数量必须是数字。", http_status=400) from e
            if amount <= 0:
                raise AppError("INVALID_REQUEST", "额度数量必须大于 0。", http_status=400)
            cleaned_items.append(
                {
                    "metric": metric,
                    "window": str(entry.get("window") or "month"),
                    "amount": amount,
                    "valid_days": int(entry.get("valid_days") or 0),
                    "note": str(entry.get("note") or "")[:120],
                }
            )
        row.items = cleaned_items
    if sort_order is not None:
        row.sort_order = int(sort_order)
    if active is not None:
        row.active = bool(active)
    if note is not None:
        row.note = note.strip()[:200] or None
    db.commit()
    db.refresh(row)
    payload = plan_payload(_plan_from_row(row), baseline={})
    payload["updated"] = True
    return payload


__all__ = [
    "ALLOWED_METRICS",
    "environment_baseline",
    "get_plan_by_code",
    "list_plan_payloads",
    "load_plans",
    "plan_payload",
    "seed_plans_if_empty",
    "update_plan",
]
