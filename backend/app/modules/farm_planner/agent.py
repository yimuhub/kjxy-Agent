"""农事规划 Agent（code="farm_planner"，version="1.0.0"）。

- definition 来自 app.modules.agents.definitions.get_definition("farm_planner")。
- run(input_data, context)：校验输入 → 取 context.metadata["db"] →
  service.generate_plan → 封装 AgentResult。
- 本期只生成建议，不自动派单（硬规则，见 service.DISPATCH_NOTE）。

文件末尾自注册到全局注册表；agents/router.py 按 code import 本模块触发注册。
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.agents.definitions import get_definition
from app.modules.agents.schemas import AgentResult
from app.modules.farm_planner.schemas import PlanInput
from app.modules.farm_planner.service import generate_plan

log = get_logger(__name__)


class FarmPlannerAgent:
    code = "farm_planner"
    version = "1.0.0"
    definition = get_definition("farm_planner")

    async def run(self, input_data: dict[str, Any], context: Any) -> AgentResult:
        try:
            plan_input = PlanInput(**(input_data or {}))
        except ValidationError as e:
            raise AppError(
                "INVALID_REQUEST",
                f"农事规划输入不合法：{e.errors()[0]['msg']}。",
                http_status=400,
                details={"errors": e.errors()},
            ) from e

        yard_id = str(getattr(context, "yard_id", "") or getattr(context, "yard", "") or "")
        user_id = str(getattr(context, "actor_id", "") or getattr(context, "user_id", "") or "")
        metadata = getattr(context, "metadata", None) or {}
        db = metadata.get("db") if isinstance(metadata, dict) else None

        plan = generate_plan(db, yard_id, user_id, plan_input)
        warnings = list(plan.get("warnings", []))
        if db is None:
            log.info("farm_planner run without db session; knowledge search skipped.")

        missing = sum(
            1 for w in warnings if w.startswith("缺少") or "未检索到" in w or "未传入数据库" in w
        )
        confidence = max(0.3, round(1.0 - 0.15 * missing, 2))

        return AgentResult(
            status="succeeded",
            output=plan,
            citations=plan.get("data_sources", []),
            warnings=warnings,
            confidence=confidence,
        )


def _register_agent() -> None:
    try:
        from app.modules.agents import get_global_registry

        get_global_registry().register(FarmPlannerAgent.code, FarmPlannerAgent())
        log.info("registered agent %s v%s", FarmPlannerAgent.code, FarmPlannerAgent.version)
    except Exception as e:
        log.warning("farm_planner agent registration skipped: %s", e)


_register_agent()
