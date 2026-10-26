"""devices 模块 ORM：devices / readings / device_alert_rules / device_alerts。

- readings 只插不改（append-only 时序表），(device_id, metric, collected_at) 建
  普通索引而非唯一——允许设备断网后补报同一时刻数据；
- device_alerts.rule_id 可空：连接器连续失败自动停用产生的"健康告警"无规则来源；
- 连接器轮询状态（last_polled_at/consecutive_failures/last_error）放 devices 列上：
  高频小更新，与用户可编辑的 connector_config_json 分离，避免互相覆盖。
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, _utcnow


class Device(TimestampMixin, Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("yard_id", "name", name="uq_devices_yard_name"),)

    yard_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # 厂商/型号自由文本（"托普云农 TNSSH-cpmz"之类），仅展示
    model_hint: Mapped[str | None] = mapped_column(String(120), nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # weather|soil|pest|greenhouse|other
    device_type: Mapped[str] = mapped_column(String(20), default="other", nullable=False)
    # webhook=设备侧推送（token 鉴权）；connector=平台定时拉取
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # webhook 接入令牌的 sha256 hex；明文只在创建/轮换时返回一次
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 连接器配置（headers 值 Fernet 密文存 headers_enc；见 connector.py）
    connector_config_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # ---- 连接器轮询状态（仅 source_type=connector 使用）----
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)


class Reading(Base):
    """时序读数：只插不改；同一 (device, metric, collected_at) 允许重复（补报）。"""

    __tablename__ = "readings"
    __table_args__ = (
        Index("ix_readings_device_metric_time", "device_id", "metric", "collected_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    yard_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    device_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("devices.id"), index=True, nullable=False
    )
    # metrics.METRICS 的 code；未识别字段为 "other"
    metric: Mapped[str] = mapped_column(String(50), nullable=False)
    # 数值型读数；非数值（如风向 "NE"）时为空、值落 text
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 原始报文片段（字典外字段必留原文；字典内字段可留厂商原键名与原始值）
    raw_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False, default=_utcnow
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class DeviceAlertRule(TimestampMixin, Base):
    __tablename__ = "device_alert_rules"

    yard_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    # 空=该小院该指标全部设备生效
    device_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    metric: Mapped[str] = mapped_column(String(50), nullable=False)
    # lt（低于阈值告警，如低温霜冻）| gt（高于阈值告警，如高温/虫情）
    op: Mapped[str] = mapped_column(String(4), nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # 告警时附带的处置提示（进 notify 文案，如"提示：注意关棚保温"）
    notify_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class DeviceAlert(TimestampMixin, Base):
    __tablename__ = "device_alerts"
    __table_args__ = (
        # 去重查询（同 rule+device 30 分钟内不重复）与列表页都按时间倒序
        Index("ix_device_alerts_rule_device_time", "rule_id", "device_id", "triggered_at"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    # 空=系统健康类告警（如连接器连续失败自动停用）
    rule_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    device_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    # 阈值类为指标 code；健康类固定 "_health"
    metric: Mapped[str] = mapped_column(String(50), nullable=False)
    # 触发时的读数（健康类为空）
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    # 已发送的告警文案（站内展示即所用文本；也证明通知已走过 send_alert）
    notified_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 一键工单：指向 tasks 表 workorder.dispatch 任务行（本小院）；空=尚未开工单。
    # 幂等锚点——已有值的告警再次"开工单"直接返回既有工单号，不重复创建。
    workorder_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


def utcnow() -> datetime:
    return datetime.now(UTC)
