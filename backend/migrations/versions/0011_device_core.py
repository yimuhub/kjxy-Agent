"""设备接入核心：devices / readings / device_alert_rules / device_alerts（A1+A2）。

Revision ID: 0011_device_core
Revises: 0010_factory_runs
Create Date: 2026-10-19

背景（docs/plan-agri-io-and-skillhub.md A 线，用户已拍板）：
统一时序模型承接 webhook 推送与自定义连接器拉取两类通道；阈值规则比对产生
告警；只读+建议、不自动反控设备。

要点：
- devices 对 (tenant_id, name) 唯一——租户内重名直接拒绝，跨租户互不影响；
- readings 追加式写入：(gateway/device_id, metric, collected_at) 建普通复合
  索引而非唯一约束，允许断网补报；
- device_alerts.rule_id 可空：连接器连续失败自动停用的"健康告警"没有规则来源。
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_device_core"
down_revision = "0010_factory_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        # 厂商/型号自由文本（仅展示）
        sa.Column("model_hint", sa.String(120), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("location_note", sa.Text(), nullable=True),
        # weather|soil|pest|greenhouse|other
        sa.Column("device_type", sa.String(20), nullable=False, server_default="other"),
        # webhook=设备侧 token 推送 | connector=平台定时拉取厂商接口
        sa.Column("source_type", sa.String(20), nullable=False),
        # webhook 令牌 sha256 hex（明文仅创建/轮换时一次性返回）
        sa.Column("token_hash", sa.String(64), nullable=True),
        # 连接器配置：headers 值 Fernet 密文（headers_enc），其余明文；见 connector.py
        sa.Column("connector_config_json", sa.JSON(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(36), nullable=True),
        # 连接器轮询状态（高频小更新，与用户配置分列存储）
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "consecutive_failures", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("last_error", sa.String(300), nullable=True),
        sa.UniqueConstraint("tenant_id", "name", name="uq_devices_tenant_name"),
    )
    op.create_index("ix_devices_tenant_id", "devices", ["tenant_id"])

    op.create_table(
        "readings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("devices.id"), nullable=False),
        # metrics.METRICS 的 code；未识别字段固定 "other"
        sa.Column("metric", sa.String(50), nullable=False),
        # 数值读数；非数值读数（如风向 NE）value 为空、值落 text
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(20), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        # 原始报文片段（字典外字段必留原文）
        sa.Column("raw_json", sa.JSON(), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_readings_tenant_id", "readings", ["tenant_id"])
    op.create_index("ix_readings_device_id", "readings", ["device_id"])
    op.create_index("ix_readings_collected_at", "readings", ["collected_at"])
    # 注意：非唯一——设备断网补报同一时刻数据是合法场景
    op.create_index(
        "ix_readings_device_metric_time", "readings", ["device_id", "metric", "collected_at"]
    )

    op.create_table(
        "device_alert_rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        # 空=该租户该指标全部设备生效
        sa.Column("device_id", sa.String(36), nullable=True),
        sa.Column("metric", sa.String(50), nullable=False),
        # lt=低于阈值告警（霜冻）| gt=高于阈值告警（高温/虫情爆发）
        sa.Column("op", sa.String(4), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        # 处置提示文案，随告警一并推送
        sa.Column("notify_note", sa.Text(), nullable=True),
    )
    op.create_index("ix_device_alert_rules_tenant_id", "device_alert_rules", ["tenant_id"])

    op.create_table(
        "device_alerts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        # 空=系统健康类告警（连接器连续失败自动停用等）
        sa.Column("rule_id", sa.String(36), nullable=True),
        sa.Column("device_id", sa.String(36), nullable=False),
        # 阈值类=指标 code；健康类固定 "_health"
        sa.Column("metric", sa.String(50), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        # 已推送的告警文案（站内展示 + 证明通知已发）
        sa.Column("notified_text", sa.Text(), nullable=True),
    )
    op.create_index("ix_device_alerts_tenant_id", "device_alerts", ["tenant_id"])
    op.create_index("ix_device_alerts_device_id", "device_alerts", ["device_id"])
    op.create_index(
        "ix_device_alerts_rule_device_time",
        "device_alerts",
        ["rule_id", "device_id", "triggered_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_device_alerts_rule_device_time", table_name="device_alerts")
    op.drop_index("ix_device_alerts_device_id", table_name="device_alerts")
    op.drop_index("ix_device_alerts_tenant_id", table_name="device_alerts")
    op.drop_table("device_alerts")
    op.drop_index("ix_device_alert_rules_tenant_id", table_name="device_alert_rules")
    op.drop_table("device_alert_rules")
    op.drop_index("ix_readings_device_metric_time", table_name="readings")
    op.drop_index("ix_readings_collected_at", table_name="readings")
    op.drop_index("ix_readings_device_id", table_name="readings")
    op.drop_index("ix_readings_tenant_id", table_name="readings")
    op.drop_table("readings")
    op.drop_index("ix_devices_tenant_id", table_name="devices")
    op.drop_table("devices")
