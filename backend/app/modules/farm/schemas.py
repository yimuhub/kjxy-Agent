"""农业台账 Schema。"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlotCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    crop: str = Field(default="", max_length=60)
    area_mu: float | None = Field(default=None, gt=0, le=100000)
    location: str = Field(default="", max_length=200)
    soil_type: str = Field(default="", max_length=40)
    irrigation_type: str = Field(default="", max_length=40)
    status: str = Field(default="active", max_length=16)
    notes: str = Field(default="", max_length=2000)


class PlotUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    crop: str | None = Field(default=None, max_length=60)
    area_mu: float | None = Field(default=None, gt=0, le=100000)
    location: str | None = Field(default=None, max_length=200)
    soil_type: str | None = Field(default=None, max_length=40)
    irrigation_type: str | None = Field(default=None, max_length=40)
    status: str | None = Field(default=None, max_length=16)
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _not_empty(self) -> "PlotUpdate":
        if not self.model_fields_set:
            raise ValueError("没有需要更新的字段")
        return self


class PlotOut(BaseModel):
    plot_id: str
    name: str
    crop: str
    area_mu: float | None
    location: str
    soil_type: str
    irrigation_type: str
    status: str
    notes: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CycleCreate(BaseModel):
    crop: str = Field(..., min_length=1, max_length=60)
    variety: str = Field(default="", max_length=60)
    planted_on: date | None = None
    expected_harvest_on: date | None = None
    status: str = Field(default="growing", max_length=16)
    notes: str = Field(default="", max_length=2000)


class CycleUpdate(BaseModel):
    crop: str | None = Field(default=None, min_length=1, max_length=60)
    variety: str | None = Field(default=None, max_length=60)
    planted_on: date | None = None
    expected_harvest_on: date | None = None
    status: str | None = Field(default=None, max_length=16)
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _not_empty(self) -> "CycleUpdate":
        if not self.model_fields_set:
            raise ValueError("没有需要更新的字段")
        return self


class CycleOut(BaseModel):
    cycle_id: str
    plot_id: str
    crop: str
    variety: str
    planted_on: date | None
    expected_harvest_on: date | None
    status: str
    notes: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RecordCreate(BaseModel):
    plot_id: str = Field(..., min_length=1, max_length=36)
    cycle_id: str | None = Field(default=None, max_length=36)
    kind: str = Field(..., min_length=1, max_length=20)
    occurred_on: date
    title: str = Field(default="", max_length=120)
    content: str = Field(default="", max_length=5000)
    quantity: float | None = Field(default=None, gt=0, le=1_000_000)
    unit: str = Field(default="", max_length=20)
    operator_name: str = Field(default="", max_length=60)
    metadata_json: dict | None = None


class RecordUpdate(BaseModel):
    cycle_id: str | None = Field(default=None, max_length=36)
    kind: str | None = Field(default=None, max_length=20)
    occurred_on: date | None = None
    title: str | None = Field(default=None, max_length=120)
    content: str | None = Field(default=None, max_length=5000)
    quantity: float | None = Field(default=None, gt=0, le=1_000_000)
    unit: str | None = Field(default=None, max_length=20)
    operator_name: str | None = Field(default=None, max_length=60)
    metadata_json: dict | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> "RecordUpdate":
        if not self.model_fields_set:
            raise ValueError("没有需要更新的字段")
        return self


class RecordOut(BaseModel):
    record_id: str
    plot_id: str
    plot_name: str
    cycle_id: str | None
    kind: str
    kind_label: str
    occurred_on: date
    title: str
    content: str
    quantity: float | None
    unit: str
    operator_name: str
    metadata_json: dict | None
    created_at: datetime
    updated_at: datetime


class ReportRequest(BaseModel):
    kind: str = Field(default="log", description="log|weekly|quarterly")
    start: date
    end: date
    plot_id: str | None = Field(default=None, max_length=36)

    @model_validator(mode="after")
    def _range(self) -> "ReportRequest":
        if self.end < self.start:
            raise ValueError("结束日期不能早于开始日期")
        if (self.end - self.start).days > 366:
            raise ValueError("单次报表区间不能超过一年")
        return self


class ReportOut(BaseModel):
    title: str
    kind: str
    start: date
    end: date
    records: int
    plots: int
    content_md: str
    stats: dict
