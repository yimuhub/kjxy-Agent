"""设备数据智能分析（v3.23）

新增 ``device_analyses``：一次分析 = 一个时间窗 + 一组规则发现（findings）+
数据质量 + 结论文本 + 可选模型成本。同一设备同一天同一粒度只留一条（幂等）。

纯增量迁移，可完整 downgrade。

Revision ID: 0050
Revises: 0049
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0050_device_analyses"
down_revision = "0049_skill_auto_upgrade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_analyses",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("yard_id", sa.String(length=36), nullable=False),
        sa.Column("device_id", sa.String(length=36), nullable=False),
        sa.Column("window_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("granularity", sa.String(length=8), nullable=False),
        # 幂等键：设备 + 窗口结束日 + 粒度
        sa.Column("analysis_key", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        # info | watch | warning | critical（status=insufficient 时为空值无意义）
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("findings_json", sa.JSON(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=True),
        sa.Column("quality_json", sa.JSON(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("device_id", "analysis_key", name="uq_device_analyses_key"),
    )
    op.create_index("ix_device_analyses_yard_id", "device_analyses", ["yard_id"])
    op.create_index("ix_device_analyses_device_id", "device_analyses", ["device_id"])
    op.create_index("ix_device_analyses_yard_created", "device_analyses", ["yard_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_device_analyses_yard_created", table_name="device_analyses")
    op.drop_index("ix_device_analyses_device_id", table_name="device_analyses")
    op.drop_index("ix_device_analyses_yard_id", table_name="device_analyses")
    op.drop_table("device_analyses")
