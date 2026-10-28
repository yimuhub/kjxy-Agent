"""农业台账模型（v3.8）：地块、种植周期、农事记录。

设计口径：
- 一切数据强制带 ``yard_id``，跨院查询一律 404；
- 记录不覆盖历史：编辑保留 ``updated_at``，删除为物理删除并写审计；
- 数量/单位分开存，避免“30 公斤”这类字符串无法汇总；
- 周期单独成表，支持同一地块一年内多茬（早稻/晚稻/冬种）。
"""

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

PLOT_STATUSES = ("active", "fallow", "archived")
CYCLE_STATUSES = ("growing", "harvested", "failed")

#: 农事记录类型（受控词表；前端展示名见 RECORD_KIND_LABELS）
RECORD_KINDS = (
    "sowing",
    "fertilizing",
    "irrigation",
    "pesticide",
    "weeding",
    "scouting",
    "harvest",
    "other",
)

RECORD_KIND_LABELS: dict[str, str] = {
    "sowing": "播种/移栽",
    "fertilizing": "施肥",
    "irrigation": "灌溉",
    "pesticide": "用药",
    "weeding": "除草",
    "scouting": "巡田",
    "harvest": "采收",
    "other": "其他",
}


class FarmPlot(Base, TimestampMixin):
    __tablename__ = "farm_plots"
    __table_args__ = (
        UniqueConstraint("yard_id", "name", name="uq_farm_plots_yard_name"),
        # 离线重放：同一台设备同一次操作只落一条（NULL 视为互不相等，不占用名额）
        UniqueConstraint("yard_id", "client_op_id", name="uq_farm_plots_yard_op"),
        Index("ix_farm_plots_yard_status", "yard_id", "status"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    crop: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    area_mu: Mapped[float | None] = mapped_column(Float, nullable=True)
    location: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    soil_type: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    irrigation_type: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # 乐观并发：每次成功写入 +1，离线客户端拿旧版本保存时用于冲突检测
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    client_op_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class FarmCycle(Base, TimestampMixin):
    __tablename__ = "farm_cycles"
    __table_args__ = (
        UniqueConstraint("yard_id", "client_op_id", name="uq_farm_cycles_yard_op"),
        Index("ix_farm_cycles_yard_plot", "yard_id", "plot_id"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    plot_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    crop: Mapped[str] = mapped_column(String(60), nullable=False)
    variety: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    planted_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_harvest_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="growing")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    client_op_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class FarmRecord(Base, TimestampMixin):
    __tablename__ = "farm_records"
    __table_args__ = (
        UniqueConstraint("yard_id", "client_op_id", name="uq_farm_records_yard_op"),
        Index("ix_farm_records_yard_date", "yard_id", "occurred_on"),
        Index("ix_farm_records_yard_plot_date", "yard_id", "plot_id", "occurred_on"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    plot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    cycle_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    operator_name: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    # 照片/附件等扩展信息，先留结构位，不把大对象写进主表
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    client_op_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


DIAGNOSIS_STATUSES = ("open", "expert_review", "confirmed", "closed")

#: 图像识别接入点状态：当前只保存图片引用，不做识别
VISION_STATUSES = ("not_configured", "pending", "done", "failed")


class CropDiagnosis(Base, TimestampMixin):
    """作物诊断记录（v3.9）：保存输入、诊断辅助输出与专家复核闭环。"""

    __tablename__ = "crop_diagnoses"
    __table_args__ = (
        Index("ix_crop_diagnoses_yard_created", "yard_id", "created_at"),
        Index("ix_crop_diagnoses_yard_status", "yard_id", "status"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    plot_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    crop: Mapped[str] = mapped_column(String(100), nullable=False)
    variety: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    growth_stage: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    region: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    symptoms: Mapped[str] = mapped_column(Text, nullable=False)
    image_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    environment_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    output_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    needs_expert_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    vision_status: Mapped[str] = mapped_column(String(16), nullable=False, default="not_configured")
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    expert_reviewer_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    expert_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    final_diagnosis: Mapped[str] = mapped_column(Text, nullable=False, default="")
    expert_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DiagnosisFollowUp(Base, TimestampMixin):
    """诊断跟进：现场观察、处置与结果，按时间追加。"""

    __tablename__ = "diagnosis_followups"
    __table_args__ = (Index("ix_diagnosis_followups_yard_diag", "yard_id", "diagnosis_id"),)

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    diagnosis_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    image_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class PesticideProduct(Base, TimestampMixin):
    """本院登记药剂：只记录标签信息与安全间隔期，不替代当地登记查询。"""

    __tablename__ = "pesticide_products"
    __table_args__ = (UniqueConstraint("yard_id", "name", name="uq_pesticide_products_yard_name"),)

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    active_ingredient: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    registered_crops: Mapped[list | None] = mapped_column(JSON, nullable=True)
    pre_harvest_interval_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    label_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


WORK_ORDER_STATUSES = ("open", "assigned", "in_progress", "done", "closed", "cancelled")
TERMINAL_WORK_ORDER_STATUSES = ("done", "closed", "cancelled")
WORK_ORDER_PRIORITIES = ("low", "normal", "high", "urgent")
ALERT_SEVERITIES = ("low", "normal", "high", "critical")


class FarmAlert(Base, TimestampMixin):
    """小院告警：虫情/天气/设备等事件，可转工单。"""

    __tablename__ = "farm_alerts"
    __table_args__ = (Index("ix_farm_alerts_yard_created", "yard_id", "created_at"),)

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    plot_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="new")
    work_order_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class WorkOrder(Base, TimestampMixin):
    """作业工单：从告警/诊断/人工创建，支持指派、回执、升级。"""

    __tablename__ = "work_orders"
    __table_args__ = (
        UniqueConstraint("yard_id", "client_op_id", name="uq_work_orders_yard_op"),
        Index("ix_work_orders_yard_status", "yard_id", "status"),
        Index("ix_work_orders_yard_due", "yard_id", "due_on"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    source_ref: Mapped[str | None] = mapped_column(String(36), nullable=True)
    plot_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    assignee_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    assignee_name: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    escalation_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resolution: Mapped[str] = mapped_column(Text, nullable=False, default="")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    client_op_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class WorkOrderEvent(Base, TimestampMixin):
    """工单事件流：创建、指派、状态变更、评论、回执、升级，只追加不改写。"""

    __tablename__ = "work_order_events"
    __table_args__ = (
        # 回执/评论的离线重放去重键
        UniqueConstraint("yard_id", "client_op_id", name="uq_work_order_events_yard_op"),
        Index("ix_work_order_events_yard_order", "yard_id", "work_order_id"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    work_order_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    client_op_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
