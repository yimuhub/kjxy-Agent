"""自助付费闭环：订单支付字段与对公回单核销（v3.79，审查 D-17）。

在 ``billing_orders`` 上补 5 列，记录"这笔订单走的哪个渠道、渠道流水号是��、
什么时候付的、用户提交的回单号与核销状态"。

- ``payment_trade_no`` 建唯一索引：**同一渠道流水号只能到账一次**，
  渠道重复回调（各家网关都会重试）不会重复发放额度。
- ``voucher_status`` 区分"已提交待核（submitted）/ 金额不符转人工（manual）"，
  运营在订单列表里一眼能看出哪些还压着。到账只走平台「确认到账」链路，
  **没有**"金额匹配即自动到账"的状态——那是可被伪造回单利用的荣誉制
  （2026-11-13 二次深审移除，字段语义见 billing/service.submit_voucher）。

纯新增列，SQLite/Postgres 均可直接执行；dev 环境 create_all 已按新模型建表，
存在即跳过。

Revision ID: 0085_billing_payment_fields
Revises: 0084_free_deleted_agent_code
"""

import sqlalchemy as sa
from alembic import op

revision = "0085_billing_payment_fields"
down_revision = "0084_free_deleted_agent_code"
branch_labels = None
depends_on = None

_TABLE = "billing_orders"

_COLUMNS = (
    ("payment_provider", sa.String(24), True),
    ("payment_trade_no", sa.String(64), True),
    ("payment_paid_at", sa.DateTime(timezone=True), True),
    ("voucher_no", sa.String(64), True),
    ("voucher_status", sa.String(16), True),
)


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _has_column(bind, table: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table):
        return False
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, _TABLE):
        return
    for name, col_type, nullable in _COLUMNS:
        if _has_column(bind, _TABLE, name):
            continue
        op.add_column(_TABLE, sa.Column(name, col_type, nullable=nullable))
    # 渠道流水号唯一索引：同一笔渠道支付重复回调只允许到账一次
    with op.batch_alter_table(_TABLE) as batch:
        try:
            batch.create_index(
                "uq_billing_orders_payment_trade_no", ["payment_trade_no"], unique=True
            )
        except Exception:  # noqa: BLE001 - 已有历史数据重复时跳过建索引
            pass


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, _TABLE):
        return
    with op.batch_alter_table(_TABLE) as batch:
        try:
            batch.drop_index("uq_billing_orders_payment_trade_no")
        except Exception:  # noqa: BLE001
            pass
    for name, _col_type, _nullable in reversed(_COLUMNS):
        if _has_column(bind, _TABLE, name):
            op.drop_column(_TABLE, name)
