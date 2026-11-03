"""组织架构表（v3.57）

``org_units`` 自关联组织树；``org_positions`` 岗位可软关联 Agent。
全部按 yard_id 隔离，不存任何密钥或个人敏感信息。
"""

import sqlalchemy as sa
from alembic import op

revision = "0065_org_structure"
down_revision = "0064_cluster_nodes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "org_units",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("parent_id", sa.String(36), nullable=True),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="department"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("updated_by", sa.String(36), nullable=True),
        sa.UniqueConstraint("yard_id", "code", name="uq_org_units_yard_code"),
    )
    op.create_index("ix_org_units_yard_id", "org_units", ["yard_id"])
    op.create_index("ix_org_units_parent_id", "org_units", ["parent_id"])
    op.create_index("ix_org_units_yard_parent", "org_units", ["yard_id", "parent_id"])
    op.create_index("ix_org_units_yard_sort", "org_units", ["yard_id", "sort_order"])

    op.create_table(
        "org_positions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("org_unit_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(80), nullable=False),
        sa.Column("responsibilities", sa.Text(), nullable=True),
        sa.Column("headcount", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("agent_id", sa.String(36), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("updated_by", sa.String(36), nullable=True),
    )
    op.create_index("ix_org_positions_yard_id", "org_positions", ["yard_id"])
    op.create_index("ix_org_positions_org_unit_id", "org_positions", ["org_unit_id"])
    op.create_index("ix_org_positions_agent_id", "org_positions", ["agent_id"])
    op.create_index("ix_org_positions_yard_unit", "org_positions", ["yard_id", "org_unit_id"])
    op.create_index("ix_org_positions_yard_sort", "org_positions", ["yard_id", "sort_order"])


def downgrade() -> None:
    op.drop_index("ix_org_positions_yard_sort", table_name="org_positions")
    op.drop_index("ix_org_positions_yard_unit", table_name="org_positions")
    op.drop_index("ix_org_positions_agent_id", table_name="org_positions")
    op.drop_index("ix_org_positions_org_unit_id", table_name="org_positions")
    op.drop_index("ix_org_positions_yard_id", table_name="org_positions")
    op.drop_table("org_positions")
    op.drop_index("ix_org_units_yard_sort", table_name="org_units")
    op.drop_index("ix_org_units_yard_parent", table_name="org_units")
    op.drop_index("ix_org_units_parent_id", table_name="org_units")
    op.drop_index("ix_org_units_yard_id", table_name="org_units")
    op.drop_table("org_units")
