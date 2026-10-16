"""农事规划 Agent 模块。

导出 FarmPlannerAgent 与业务入口；import 本模块即触发 agent.py 末尾
向全局 Agent 注册表的自注册（agents/router.py 按 code import 本模块）。
"""

from app.modules.farm_planner.agent import FarmPlannerAgent
from app.modules.farm_planner.schemas import PlanInput, PlanOutput
from app.modules.farm_planner.service import generate_plan

__all__ = ["FarmPlannerAgent", "PlanInput", "PlanOutput", "generate_plan"]
