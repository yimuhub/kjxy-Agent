"""科研辅助 Agent 模块：CSV 导入、字段映射、缺失值报告、描述统计与图表草案。

导入本包（或 app.modules.research_assistant.agent）即触发 Agent 向全局注册表自注册。
"""

from app.modules.research_assistant.agent import ResearchAssistantAgent
from app.modules.research_assistant.schemas import (
    AnalysisInput,
    AnalysisOutput,
    ChartDraft,
    ChartSeries,
    DescriptiveStatsItem,
    ImportReport,
)
from app.modules.research_assistant.service import analyze_csv

__all__ = [
    "ResearchAssistantAgent",
    "AnalysisInput",
    "AnalysisOutput",
    "ChartDraft",
    "ChartSeries",
    "DescriptiveStatsItem",
    "ImportReport",
    "analyze_csv",
]
