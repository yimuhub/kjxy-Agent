"""数据汇总 Agent 模块。import 时触发 agent.py 的自注册（向全局 Agent 注册表）。"""

try:
    from app.modules.data_analyst import agent  # noqa: F401
except Exception:
    pass

from app.modules.data_analyst.schemas import ReportInput, ReportOutput

__all__ = ["ReportInput", "ReportOutput", "agent"]
