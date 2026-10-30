"""分级额度与提额审批（v3.32）

- ``quota_policies``：按「小院 / Agent / Agent 分组 / 模型 / 供应商」设额度，
  指标支持 tokens/calls/cost，窗口支持 day/month，超限动作 warn/block/approve；
- ``quota_counters``：按策略维度×窗口累计用量（原子 upsert，防并发超卖）；
- ``quota_requests``：超限后的提额申请单（pending→approved/rejected/cancelled）；
- ``quota_grants``：审批通过后追加的临时额度（带过期时间）；
- ``yard_agents.group_name``：Agent 分组，让「一类 Agent」共用一条额度；
- ``suppliers.price_in_per_1k/price_out_per_1k``：供应商单价，支撑金额额度。

纯增量迁移，可完整 downgrade。

Revision ID: 0055_quota_policies
Revises: 0054_farm_agent_automation
Create Date: 2026-10-30
"""

import sqlalchemy as sa
from alembic import op

revision = "0055_quota_policies"
down_revision = "0054_farm_agent_automation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quota_policies",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("yard_id", sa.String(length=36), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_key", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("metric", sa.String(length=16), nullable=False, server_default="tokens"),
        sa.Column("window", sa.String(length=8), nullable=False, server_default="month"),
        sa.Column("limit_value", sa.Float(), nullable=False, server_default="0"),
        sa.Column("on_exceed", sa.String(length=16), nullable=False, server_default="approve"),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "yard_id",
            "scope_type",
            "scope_key",
            "metric",
            "window",
            name="uq_quota_policies_scope",
        ),
    )
    op.create_index("ix_quota_policies_yard_id", "quota_policies", ["yard_id"])

    op.create_table(
        "quota_counters",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("yard_id", sa.String(length=36), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_key", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("metric", sa.String(length=16), nullable=False),
        sa.Column("window", sa.String(length=8), nullable=False),
        sa.Column("window_start", sa.String(length=10), nullable=False),
        sa.Column("used", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "yard_id",
            "scope_type",
            "scope_key",
            "metric",
            "window",
            "window_start",
            name="uq_quota_counters_scope",
        ),
    )
    op.create_index("ix_quota_counters_yard_id", "quota_counters", ["yard_id"])

    op.create_table(
        "quota_requests",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("yard_id", sa.String(length=36), nullable=False),
        sa.Column("policy_id", sa.String(length=36), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_key", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("metric", sa.String(length=16), nullable=False),
        sa.Column("window", sa.String(length=8), nullable=False),
        sa.Column("current_limit", sa.Float(), nullable=False, server_default="0"),
        sa.Column("requested_limit", sa.Float(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="pending"),
        sa.Column("requester_id", sa.String(length=36), nullable=False),
        sa.Column("requester_name", sa.String(length=100), nullable=True),
        sa.Column("decided_by", sa.String(length=36), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("valid_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_quota_requests_yard_id", "quota_requests", ["yard_id"])
    op.create_index("ix_quota_requests_status", "quota_requests", ["status"])

    op.create_table(
        "quota_grants",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("yard_id", sa.String(length=36), nullable=False),
        sa.Column("policy_id", sa.String(length=36), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_quota_grants_policy_id", "quota_grants", ["policy_id"])

    with op.batch_alter_table("yard_agents") as batch:
        batch.add_column(sa.Column("group_name", sa.String(length=40), nullable=True))
    op.create_index("ix_yard_agents_group", "yard_agents", ["yard_id", "group_name"])

    with op.batch_alter_table("suppliers") as batch:
        batch.add_column(sa.Column("price_in_per_1k", sa.Float(), nullable=True))
        batch.add_column(sa.Column("price_out_per_1k", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("suppliers") as batch:
        batch.drop_column("price_out_per_1k")
        batch.drop_column("price_in_per_1k")

    op.drop_index("ix_yard_agents_group", table_name="yard_agents")
    with op.batch_alter_table("yard_agents") as batch:
        batch.drop_column("group_name")

    op.drop_index("ix_quota_grants_policy_id", table_name="quota_grants")
    op.drop_table("quota_grants")

    op.drop_index("ix_quota_requests_status", table_name="quota_requests")
    op.drop_index("ix_quota_requests_yard_id", table_name="quota_requests")
    op.drop_table("quota_requests")

    op.drop_index("ix_quota_counters_yard_id", table_name="quota_counters")
    op.drop_table("quota_counters")

    op.drop_index("ix_quota_policies_yard_id", table_name="quota_policies")
    op.drop_table("quota_policies")
