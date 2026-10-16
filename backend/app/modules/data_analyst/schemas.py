"""数据汇总 Agent 的输入/输出 Pydantic 模型。

规范依据 V3.0 §12.4：报表中每个关键数字必须可回溯到查询条件、
时间范围和数据源（见 Metric.trace）。
"""

from typing import Any

from pydantic import BaseModel, Field

#: 允许的数据源。tasks/knowledge 指向各自模块的业务表，
#: llm_calls 指向 app.modules.llm.models.LLMCall。
ALLOWED_SOURCES: tuple[str, ...] = ("tasks", "knowledge", "llm_calls")

#: 报表格式版本（ReportOutput.version 固定值）。
REPORT_VERSION = "1.0.0"

#: 默认统计窗口：近 30 天。
DEFAULT_LOOKBACK_DAYS = 30


class TimeRange(BaseModel):
    """时间范围：ISO 日期字符串（YYYY-MM-DD），起止均为可选。"""

    start: str | None = Field(default=None, description="起始日期（含），如 2026-09-16")
    end: str | None = Field(default=None, description="结束日期（含），如 2026-10-16")


class ReportInput(BaseModel):
    """报表请求参数。"""

    sources: list[str] = Field(
        default_factory=lambda: list(ALLOWED_SOURCES),
        description=f"数据源子集，可选值：{', '.join(ALLOWED_SOURCES)}",
    )
    time_range: TimeRange = Field(default_factory=TimeRange)
    group_by: str = Field(default="day", description="时间分组粒度，目前仅支持 day")


class MetricTrace(BaseModel):
    """单个指标的可回溯信息：查询了哪张表、什么过滤条件、什么时间范围。"""

    source: str = Field(description="数据源表名：tasks / knowledge_documents / llm_calls")
    filters: dict[str, Any] = Field(description="实际生效的查询过滤条件")
    time_range: dict[str, str | None] = Field(description="实际生效的时间范围")


class Metric(BaseModel):
    name: str
    value: float | int
    unit: str
    trace: MetricTrace


class ReportTable(BaseModel):
    title: str
    columns: list[str]
    rows: list[list[Any]]


class ReportOutput(BaseModel):
    report_id: str
    generated_at: str
    parameters: dict[str, Any]
    version: str = REPORT_VERSION
    metrics: list[Metric]
    tables: list[ReportTable]
    warnings: list[str]
