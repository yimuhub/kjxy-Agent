"""农事规划 Agent 的输入/输出数据模型（Pydantic v2）。

对应开发文档 V3.0 §12.2：计划草案必须明确区分"建议"（suggestions）
与"已创建工单"（work_orders）——本期只生成建议，不自动派单。
"""

from datetime import date
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class TimeWindow(BaseModel):
    start: date
    end: date


class MaterialEstimate(BaseModel):
    name: str = Field(description="物资名称")
    quantity: float = Field(ge=0, description="估算数量")
    unit: str = Field(description="单位；未提供地块面积时为单位面积用量（如 公斤/亩）")
    per_mu: bool = Field(default=False, description="是否为按亩估算（未提供面积时为 True）")


class Suggestion(BaseModel):
    task_code: str = Field(description="任务编码（用于去重/跳过已完成农事）")
    task_name: str = Field(description="农事任务名称")
    stage: str = Field(description="所属生育期")
    time_window: TimeWindow
    materials: list[MaterialEstimate] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list, description="依赖条件")
    risks: list[str] = Field(default_factory=list, description="风险提示")
    note: str = Field(default="", description="补充说明")


class PlanPeriod(BaseModel):
    start: date
    end: date

    @model_validator(mode="after")
    def _check_order(self) -> "PlanPeriod":
        if self.end < self.start:
            raise ValueError("计划周期结束日期不能早于开始日期。")
        return self


class PlanInput(BaseModel):
    plot_id: str = Field(min_length=1, description="地块标识")
    plot_area_mu: float | None = Field(
        default=None, gt=0, description="地块面积（亩），用于物资估算"
    )
    crop: str = Field(min_length=1, description="作物")
    variety: str | None = Field(default=None, description="品种")
    growth_stage: str = Field(min_length=1, description="当前生育期")
    plan_period: PlanPeriod = Field(description="计划周期")
    weather: dict[str, Any] | None = Field(default=None, description="当地天气（可选）")
    soil_moisture: dict[str, Any] | None = Field(default=None, description="墒情（可选）")
    completed_tasks: list[str] = Field(
        default_factory=list, description="已完成农事（任务编码或名称）"
    )
    resource_constraints: list[str] = Field(default_factory=list, description="资源约束")
    previous_plan: dict[str, Any] | None = Field(
        default=None, description="上一版计划（无状态版本链）"
    )
    change_reason: str | None = Field(default=None, description="计划更新原因")

    @field_validator("plot_id", "crop", "growth_stage")
    @classmethod
    def _strip_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空。")
        return value


class DataSource(BaseModel):
    document_id: str | None = None
    chunk_id: str | None = None
    title: str | None = None
    source: str | None = None
    quote: str = Field(default="", description="引用片段（截断）")
    score: float | None = None


class PlanOutput(BaseModel):
    plan_id: str = Field(description="计划 ID")
    version: int = Field(ge=1, description="版本号；基于 previous_plan 递增")
    crop: str
    variety: str | None = None
    growth_stage: str
    plan_period: PlanPeriod
    suggestions: list[Suggestion] = Field(default_factory=list, description="农事建议（非工单）")
    work_orders: list[dict[str, Any]] = Field(
        default_factory=list, description="已创建工单；本期恒为空，不自动派单"
    )
    completed_skipped: list[str] = Field(
        default_factory=list, description="因已完成而跳过的任务编码"
    )
    data_sources: list[DataSource] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    change_reason: str | None = Field(default=None, description="本次更新原因")
    dispatch_note: str = Field(default="", description="派单说明：建议与工单的区分规则")
