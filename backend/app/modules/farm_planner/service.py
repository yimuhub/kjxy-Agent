"""农事规划业务逻辑：基于规则模板生成计划草案。

- 不调用真实 LLM：任务时间窗由"作物生育期 + 计划周期"按规则模板推算。
- 用 knowledge.search 检索小院知识库，补充农事要点作为 data_sources。
- 天气/土壤/地块数据缺失时在 warnings 中明确提示，绝不编造。
- 无状态：不建表、不写库；版本链由调用方经 previous_plan 传递。
- 模板数据在 templates.py（与工作区 service.py 内嵌数据一致）。
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from app.core.logging import get_logger
from app.modules.farm_planner.schemas import (
    DataSource,
    MaterialEstimate,
    PlanInput,
    PlanOutput,
    Suggestion,
    TimeWindow,
)
from app.modules.farm_planner.templates import GENERIC_TASKS, TASK_TEMPLATES

log = get_logger(__name__)

DISPATCH_NOTE = (
    "本计划仅包含农事建议（suggestions），未自动派单。"
    "如需将某条建议转为工单，请经 tasks 服务创建 workorder.dispatch 类型任务，"
    "其状态将为“已创建待审批”（awaiting_review），须经人工审批后方可执行。"
)


def _search_knowledge(db: Any, yard_id: str, query: str, top_k: int = 5) -> list[dict]:
    """小院内知识检索（农事要点补充）。异常时返回空列表，绝不阻断计划生成。"""
    if db is None:
        return []
    try:
        from app.core.deps import YardContext
        from app.modules.knowledge.service import search as knowledge_search

        tctx = YardContext(yard_id=yard_id, user_id="", roles=[], request_id="farm_planner")
        hits = knowledge_search(db, tctx, query, top_k)
    except Exception as e:
        log.warning("farm_planner knowledge search failed: %s", e)
        return []
    result = []
    for hit in hits or []:
        quote = str(getattr(hit, "quote", "") or "")[:120]
        result.append(
            {
                "document_id": getattr(hit, "document_id", None),
                "chunk_id": getattr(hit, "chunk_id", None),
                "title": getattr(hit, "title", None),
                "source": getattr(hit, "source", None),
                "quote": quote,
                "score": getattr(hit, "score", None),
            }
        )
    return result


def _pick_stages(crop: str, growth_stage: str) -> tuple[list[dict[str, Any]], bool, bool]:
    """按作物+生育期挑选阶段模板。返回 (阶段列表, 作物是否已知, 生育期是否命中)。"""
    stages = TASK_TEMPLATES.get(crop)
    if stages is None:
        generic = [{"stage": t["stage"], "tasks": [t]} for t in GENERIC_TASKS]
        return generic, False, False
    idx = next((i for i, s in enumerate(stages) if s["stage"] == growth_stage), None)
    if idx is None:
        return stages, True, False
    return stages[idx:], True, True


def _build_time_window(period_start, period_end, offset_days: int, window_days: int) -> TimeWindow:
    """由计划周期 + 偏移推算任务时间窗，并钳制在计划周期内。"""
    start = period_start + timedelta(days=offset_days)
    end = start + timedelta(days=max(window_days - 1, 0))
    if start > period_end:
        start = period_end
    if end > period_end:
        end = period_end
    return TimeWindow(start=start, end=end)


def _estimate_materials(
    materials: list[dict[str, Any]], area_mu: float | None
) -> list[MaterialEstimate]:
    """物资估算：有面积则按亩折算总量，无面积则保留每亩用量。"""
    estimates = []
    for item in materials:
        per_mu = float(item.get("per_mu", 0) or 0)
        unit = str(item.get("unit", "") or "")
        if area_mu:
            estimates.append(
                MaterialEstimate(
                    name=str(item.get("name", "")),
                    quantity=round(per_mu * area_mu, 2),
                    unit=unit,
                    per_mu=False,
                )
            )
        else:
            estimates.append(
                MaterialEstimate(
                    name=str(item.get("name", "")),
                    quantity=per_mu,
                    unit=f"{unit}/亩" if unit else "每亩",
                    per_mu=True,
                )
            )
    return estimates


def generate_plan(
    db: Any, yard_id: str, user_id: str, plan_input: PlanInput | dict[str, Any]
) -> dict[str, Any]:
    """生成农事计划草案（纯函数，无 DB 写入）。返回 PlanOutput.model_dump()。"""
    if isinstance(plan_input, dict):
        plan_input = PlanInput(**plan_input)
    inp: PlanInput = plan_input
    warnings: list[str] = []
    if not inp.weather:
        warnings.append(
            "缺少天气数据：无法结合未来天气调整灌溉/植保时间窗，执行前请人工核对天气预报。"
        )
    if not inp.soil_moisture:
        warnings.append("缺少墒情数据：灌溉建议为通用时间窗，执行前请实测土壤墒情。")
    if inp.plot_area_mu is None:
        warnings.append("未提供地块面积：物资数量按单位面积（每亩）估算，请按实际面积折算。")
    if inp.resource_constraints:
        constraints = "；".join(inp.resource_constraints)
        warnings.append(f"已记录资源约束（{constraints}），建议执行前复核人力/机械/资金是否满足。")
    stages, crop_known, stage_hit = _pick_stages(inp.crop, inp.growth_stage)
    if not crop_known:
        warnings.append(
            f"作物“{inp.crop}”暂无专用规则模板，已使用通用农事模板（播种/施肥/灌溉/植保/收获），建议结合当地农技资料人工复核。"
        )
    elif not stage_hit:
        known = "、".join(s["stage"] for s in stages)
        warnings.append(
            f"生育期“{inp.growth_stage}”未命中“{inp.crop}”的已知生育期（{known}），已按全生育期模板生成，请人工确认当前生育期并调整。"
        )
    completed = {str(c).strip() for c in (inp.completed_tasks or []) if str(c).strip()}
    suggestions: list[Suggestion] = []
    completed_skipped: list[str] = []
    period = inp.plan_period
    for stage in stages:
        stage_name = stage["stage"]
        for task in stage["tasks"]:
            code = str(task.get("code", ""))
            name = str(task.get("name", ""))
            if code in completed or name in completed:
                completed_skipped.append(code or name)
                continue
            window = _build_time_window(
                period.start,
                period.end,
                int(task.get("offset_days", 0)),
                int(task.get("window_days", 7)),
            )
            suggestions.append(
                Suggestion(
                    task_code=code,
                    task_name=name,
                    stage=stage_name,
                    time_window=window,
                    materials=_estimate_materials(task.get("materials", []), inp.plot_area_mu),
                    dependencies=[str(d) for d in task.get("dependencies", [])],
                    risks=[str(r) for r in task.get("risks", [])],
                    note="",
                )
            )
    query = f"{inp.crop} {inp.growth_stage} 农事管理"
    hits = _search_knowledge(db, yard_id, query)
    data_sources = [DataSource(**h) for h in hits]
    if not hits:
        warnings.append(
            "小院知识库中未检索到与“"
            f"{query}"
            "”相关的农事要点，data_sources 为空；计划仅基于内置规则模板生成。"
        )
    if db is None:
        warnings.append("未传入数据库会话：已跳过知识库检索，data_sources 为空。")
    version = 1
    change_reason = inp.change_reason
    if inp.previous_plan:
        try:
            version = int(inp.previous_plan.get("version", 0) or 0) + 1
        except (TypeError, ValueError):
            version = 1
        if not change_reason:
            change_reason = "计划更新（未说明原因）"
        warnings.append(f"本次为计划更新：{change_reason}，版本号 v{version}。")
    plan = PlanOutput(
        plan_id=f"plan-{uuid.uuid4().hex[:12]}",
        version=version,
        crop=inp.crop,
        variety=inp.variety,
        growth_stage=inp.growth_stage,
        plan_period=period,
        suggestions=suggestions,
        work_orders=[],
        completed_skipped=completed_skipped,
        data_sources=data_sources,
        warnings=warnings,
        change_reason=change_reason,
        dispatch_note=DISPATCH_NOTE,
    )
    log.info(
        "farm_planner plan generated: yard=%s user=%s crop=%s stage=%s suggestions=%d",
        yard_id,
        user_id,
        inp.crop,
        inp.growth_stage,
        len(suggestions),
    )
    return plan.model_dump()
