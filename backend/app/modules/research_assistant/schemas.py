"""科研辅助 Agent 的输入/输出数据模型（Pydantic v2），对应开发文档 V3.0 §12.3。

硬规则：
- 只做描述统计：不计算 p 值、不做显著性检验、不引用文献。
- 输出必须携带统计假设（assumptions）与研究人员审核标记（needs_review）。
"""

from pydantic import BaseModel, Field


class AnalysisInput(BaseModel):
    csv_text: str = Field(description="CSV 文本内容（含表头行）")
    field_mapping: dict[str, str] = Field(
        default_factory=dict, description="字段映射：原列名 -> 分析用列名"
    )
    analysis_type: str = Field(
        default="descriptive", description="分析类型；当前仅支持 descriptive（描述统计）"
    )


class ImportReport(BaseModel):
    rows: int = Field(description="数据行数（不含表头）")
    columns: list[str] = Field(description="列名（含字段映射后的名称）")
    missing_values: dict[str, int] = Field(description="每列缺失值计数：列名 -> 缺失数")


class DescriptiveStatsItem(BaseModel):
    count: int = Field(description="有效数值个数")
    mean: float = Field(description="均值")
    std: float = Field(description="样本标准差（分母 n-1）；仅一个有效值时记为 0.0")
    min: float = Field(description="最小值")
    max: float = Field(description="最大值")


class ChartSeries(BaseModel):
    name: str = Field(description="系列名（列名）")
    data: list[float | None] = Field(description="按行序排列的数值；缺失值记为 null")


class ChartDraft(BaseModel):
    chart_type: str = Field(description="图表类型建议：line（趋势）")
    title: str = Field(description="图表标题草案")
    x_label: str = Field(description="横轴标签")
    y_label: str = Field(description="纵轴标签")
    series: list[ChartSeries] = Field(description="数据系列（取前 N 个数值列）")


class AnalysisOutput(BaseModel):
    import_report: ImportReport = Field(description="导入报告：行数/列名/缺失值")
    descriptive_stats: dict[str, DescriptiveStatsItem] = Field(
        description="描述统计：仅数值列，列名 -> 统计量"
    )
    chart_draft: ChartDraft | None = Field(
        default=None, description="图表草案（前端可直接渲染）；无数值列时为 null"
    )
    assumptions: list[str] = Field(description="统计方法的前提假设（必须标明）")
    warnings: list[str] = Field(description="分析过程中的提示信息")
    needs_review: bool = Field(
        default=True, description="是否需要研究人员审核：本分析为自动生成的草案，恒为 true"
    )
