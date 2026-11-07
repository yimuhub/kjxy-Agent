"""0076 billing：兑换码与开通订单（v3.71）。

支付网关接入前的自助闭环：兑换码（线下收款发码 → 小院自助兑换即时到账）
与对公转账订单（平台管理员确认到账）。到账统一写 quota_grants。

注意：dev 环境应用启动会 create_all 抢先建表（列结构与迁移完全一致），
因此建表/建索引前按存在性跳过，保证「已 create_all 的库」也能登记到本版本。
"""

import sqlalchemy as sa
from alembic import op

revision = "0076_billing_codes_orders"
down_revision = "0075_users_platform_admin"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_billing_codes_status", "billing_codes", "status"),
    ("ix_billing_orders_yard_id", "billing_orders", "yard_id"),
    ("ix_billing_orders_status", "billing_orders", "status"),
)


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _has_index(bind, table: str, name: str) -> bool:
    return any(ix["name"] == name for ix in sa.inspect(bind).get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "billing_codes"):
        op.create_table(
            "billing_codes",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("code", sa.String(24), nullable=False, unique=True),
            sa.Column("plan_code", sa.String(32), nullable=False),
            sa.Column("status", sa.String(12), nullable=False, server_default="unused"),
            sa.Column("note", sa.String(200), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("redeemed_yard_id", sa.String(36), nullable=True),
            sa.Column("redeemed_by", sa.String(36), nullable=True),
            sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_by", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if not _has_table(bind, "billing_orders"):
        op.create_table(
            "billing_orders",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("yard_id", sa.String(36), nullable=False),
            sa.Column("plan_code", sa.String(32), nullable=False),
            sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
            sa.Column("price_yuan", sa.Float(), nullable=False, server_default="0"),
            sa.Column("voucher_note", sa.Text(), nullable=True),
            sa.Column("code_id", sa.String(36), nullable=True),
            sa.Column("requester_id", sa.String(36), nullable=False),
            sa.Column("requester_name", sa.String(100), nullable=True),
            sa.Column("decided_by", sa.String(36), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("decision_note", sa.Text(), nullable=True),
            sa.Column("granted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    for name, table, column in INDEXES:
        if _has_table(bind, table) and not _has_index(bind, table, name):
            op.create_index(name, table, [column])


def downgrade() -> None:
    bind = op.get_bind()
    for name, table, _column in reversed(INDEXES):
        if _has_table(bind, table) and _has_index(bind, table, name):
            op.drop_index(name, table_name=table)
    for table in ("billing_orders", "billing_codes"):
        if _has_table(bind, table):
            op.drop_table(table)
