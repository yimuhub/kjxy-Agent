"""农业台账 Schema。"""

from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: 离线队列的幂等键：客户端生成，重放同一键只落一条（格式限安全字符，长度 8-64）
ClientOpId = Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")]


class PlotCreate(BaseModel):
    client_op_id: ClientOpId | None = None
    name: str = Field(..., min_length=1, max_length=80)
    crop: str = Field(default="", max_length=60)
    area_mu: float | None = Field(default=None, gt=0, le=100000)
    location: str = Field(default="", max_length=200)
    soil_type: str = Field(default="", max_length=40)
    irrigation_type: str = Field(default="", max_length=40)
    status: str = Field(default="active", max_length=16)
    notes: str = Field(default="", max_length=2000)


class PlotUpdate(BaseModel):
    #: 离线冲突检测：带上客户端看到的版本，后端版本不同则 409 并回传当前副本
    expected_revision: int | None = Field(default=None, ge=1)
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
    revision: int = 1
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CycleCreate(BaseModel):
    client_op_id: ClientOpId | None = None
    crop: str = Field(..., min_length=1, max_length=60)
    variety: str = Field(default="", max_length=60)
    planted_on: date | None = None
    expected_harvest_on: date | None = None
    status: str = Field(default="growing", max_length=16)
    notes: str = Field(default="", max_length=2000)


class CycleUpdate(BaseModel):
    expected_revision: int | None = Field(default=None, ge=1)
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
    revision: int = 1
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RecordCreate(BaseModel):
    client_op_id: ClientOpId | None = None
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
    expected_revision: int | None = Field(default=None, ge=1)
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
    #: manual=人工录入；agent=Agent 自动执行（界面标「Agent 自动」）
    source: str = "manual"
    agent_run_id: str | None = None
    revision: int = 1
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
    #: 先经 /farm/diagnoses/attachments 上传得到的附件 id
    attachment_ids: list[str] = Field(default_factory=list, max_length=20)
    observed_at: datetime | None = None

    @model_validator(mode="after")
    def _images(self) -> "DiagnosisCreate":
        if len(self.image_refs) > 9:
            raise ValueError("图片引用最多 9 条")
        if any(len(ref) > 500 for ref in self.image_refs):
            raise ValueError("单条图片引用不能超过 500 字符")
        return self


class DiagnosisFromDevice(BaseModel):
    """把已对接设备的读数/分析推送为一次自动诊断。"""

    device_id: str = Field(..., min_length=1, max_length=36)
    crop: str = Field(..., min_length=1, max_length=100)
    plot_id: str | None = Field(default=None, max_length=36)
    variety: str | None = Field(default=None, max_length=100)
    growth_stage: str | None = Field(default=None, max_length=100)
    region: str | None = Field(default=None, max_length=100)
    #: 人工补充观察（可选）；缺省由设备最新读数/分析自动组装症状
    symptoms: str = Field(default="", max_length=4000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=20)


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
    source: str = "manual"
    device_id: str | None = None
    device_name: str | None = None
    attachments: list[dict] = Field(default_factory=list)
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


# ---------------------------------------------------------------------------
# 告警与工单（v3.10）
# ---------------------------------------------------------------------------


class DiagnosisConsultRequest(BaseModel):
    """把诊断升级为专家会诊工单（v3.12）。"""

    expert_id: str | None = Field(default=None, max_length=36)
    note: str = Field(default="", max_length=2000)


class AlertCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=160)
    detail: str = Field(default="", max_length=4000)
    severity: str = Field(default="normal", description="low|normal|high|critical")
    source: str = Field(default="manual", max_length=32)
    plot_id: str | None = Field(default=None, max_length=36)


class AlertOut(BaseModel):
    alert_id: str
    title: str
    detail: str
    severity: str
    source: str
    plot_id: str | None
    status: str
    work_order_id: str | None
    agent_run_id: str | None = None
    created_at: datetime


class WorkOrderCreate(BaseModel):
    client_op_id: ClientOpId | None = None
    title: str = Field(..., min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    priority: str = Field(default="normal")
    plot_id: str | None = Field(default=None, max_length=36)
    source: str = Field(default="manual", max_length=32)
    source_ref: str | None = Field(default=None, max_length=36)
    assignee_id: str | None = Field(default=None, max_length=36)
    due_on: date | None = None


class WorkOrderUpdate(BaseModel):
    expected_revision: int | None = Field(default=None, ge=1)
    priority: str | None = None
    status: str | None = None
    assignee_id: str | None = Field(default=None, max_length=36)
    due_on: date | None = None
    description: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _not_empty(self) -> "WorkOrderUpdate":
        if not self.model_fields_set:
            raise ValueError("没有需要更新的字段")
        return self


class WorkOrderReceipt(BaseModel):
    #: 离线回执的幂等键：同一键重放不会重复写事件/重复改状态
    client_op_id: ClientOpId | None = None
    resolution: str = Field(..., min_length=1, max_length=4000)
    close: bool = Field(default=True, description="true=直接关闭，false=仅完成待验收")


class WorkOrderComment(BaseModel):
    note: str = Field(..., min_length=1, max_length=4000)


class WorkOrderEventOut(BaseModel):
    event_id: str
    kind: str
    note: str
    actor_id: str | None
    from_status: str | None
    to_status: str | None
    created_at: datetime


class WorkOrderOut(BaseModel):
    work_order_id: str
    title: str
    description: str
    priority: str
    status: str
    source: str
    source_ref: str | None
    plot_id: str | None
    assignee_id: str | None
    assignee_name: str
    due_on: date | None
    escalation_level: int
    resolution: str
    closed_at: datetime | None
    agent_run_id: str | None = None
    revision: int = 1
    created_at: datetime
    updated_at: datetime
    events: list[WorkOrderEventOut] = Field(default_factory=list)
