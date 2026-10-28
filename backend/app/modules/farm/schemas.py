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


# ---------------------------------------------------------------------------
# 诊断闭环（v3.9）
# ---------------------------------------------------------------------------


class DiagnosisCreate(BaseModel):
    crop: str = Field(..., min_length=1, max_length=100)
    symptoms: str = Field(..., min_length=1, max_length=4000)
    plot_id: str | None = Field(default=None, max_length=36)
    variety: str | None = Field(default=None, max_length=100)
    growth_stage: str | None = Field(default=None, max_length=100)
    region: str | None = Field(default=None, max_length=100)
    environment: dict | None = None
    image_refs: list[str] = Field(default_factory=list)
    observed_at: datetime | None = None

    @model_validator(mode="after")
    def _images(self) -> "DiagnosisCreate":
        if len(self.image_refs) > 9:
            raise ValueError("图片引用最多 9 条")
        if any(len(ref) > 500 for ref in self.image_refs):
            raise ValueError("单条图片引用不能超过 500 字符")
        return self


class DiagnosisReviewRequest(BaseModel):
    expert_note: str = Field(..., min_length=1, max_length=4000)
    final_diagnosis: str = Field(default="", max_length=4000)
    status: str = Field(default="confirmed", description="confirmed|closed")


class FollowUpCreate(BaseModel):
    occurred_on: date
    note: str = Field(..., min_length=1, max_length=4000)
    image_refs: list[str] = Field(default_factory=list)


class FollowUpOut(BaseModel):
    followup_id: str
    diagnosis_id: str
    occurred_on: date
    note: str
    image_refs: list[str] | None
    created_by: str | None
    created_at: datetime


class DiagnosisOut(BaseModel):
    diagnosis_id: str
    plot_id: str | None
    crop: str
    variety: str
    growth_stage: str
    region: str
    symptoms: str
    image_refs: list[str] | None
    environment: dict | None
    observed_at: datetime | None
    output: dict | None
    confidence: float
    needs_expert_review: bool
    status: str
    vision_status: str
    medication_guard: dict | None = None
    expert_reviewer_id: str | None
    expert_note: str
    final_diagnosis: str
    expert_reviewed_at: datetime | None
    follow_ups: list[FollowUpOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class PesticideCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    active_ingredient: str = Field(default="", max_length=120)
    registered_crops: list[str] = Field(default_factory=list)
    pre_harvest_interval_days: int = Field(default=0, ge=0, le=365)
    label_notes: str = Field(default="", max_length=2000)
    status: str = Field(default="active", max_length=16)


class PesticideUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    active_ingredient: str | None = Field(default=None, max_length=120)
    registered_crops: list[str] | None = None
    pre_harvest_interval_days: int | None = Field(default=None, ge=0, le=365)
    label_notes: str | None = Field(default=None, max_length=2000)
    status: str | None = Field(default=None, max_length=16)

    @model_validator(mode="after")
    def _not_empty(self) -> "PesticideUpdate":
        if not self.model_fields_set:
            raise ValueError("没有需要更新的字段")
        return self


class PesticideOut(BaseModel):
    pesticide_id: str
    name: str
    active_ingredient: str
    registered_crops: list[str] | None
    pre_harvest_interval_days: int
    label_notes: str
    status: str
    created_at: datetime
    updated_at: datetime


class SafeHarvestOut(BaseModel):
    pesticide_id: str
    name: str
    pre_harvest_interval_days: int
    applied_on: date
    earliest_harvest_on: date
    note: str
