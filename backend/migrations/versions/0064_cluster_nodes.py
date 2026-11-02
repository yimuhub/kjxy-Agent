"""多集群节点表（v3.48 P5）

``cluster_nodes`` 只保存节点名称、根地址与探测结论，不保存任何令牌或密钥；
探测走公开的 ``/api/v1/meta``，用于比较版本与迁移 head。
"""

import sqlalchemy as sa
from alembic import op

revision = "0064_cluster_nodes"
down_revision = "0063_commander_iterations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cluster_nodes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("base_url", sa.String(300), nullable=False),
        sa.Column("note", sa.String(200), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="never"),
        sa.Column("version", sa.String(40), nullable=False, server_default=""),
        sa.Column("migration_head", sa.String(80), nullable=False, server_default=""),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(300), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.UniqueConstraint("name", name="uq_cluster_nodes_name"),
        sa.UniqueConstraint("base_url", name="uq_cluster_nodes_base_url"),
    )
    op.create_index("ix_cluster_nodes_status", "cluster_nodes", ["status"])
    op.create_index("ix_cluster_nodes_checked", "cluster_nodes", ["last_checked_at"])


def downgrade() -> None:
    op.drop_index("ix_cluster_nodes_checked", table_name="cluster_nodes")
    op.drop_index("ix_cluster_nodes_status", table_name="cluster_nodes")
    op.drop_table("cluster_nodes")
