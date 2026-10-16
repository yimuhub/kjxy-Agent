"""虫情预警模块：import 时触发 agent.py 的自注册（向全局 Agent 注册表）。

本模块由脚手架 app.modules.agents.scaffold 自动生成。
"""

from app.modules.pest_alert.agent import PestAlertAgent  # noqa: F401 触发全局注册
from app.modules.pest_alert.schemas import PestAlertInput, PestAlertOutput
from app.modules.pest_alert.service import run_pest_alert

__all__ = ["PestAlertAgent", "PestAlertInput", "PestAlertOutput", "run_pest_alert"]
