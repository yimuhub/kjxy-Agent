"""套餐入库（v3.80，审查 D-18）。

``billing/plans.py`` 里写死的套餐目录搬到 ``billing_plans`` 表：
价格、卖点、额度项、排序与上下架此后由运营在后台改，**改价不用发版**。

迁移按当前代码里的 PLANS 播种一份初始数据（迁移必须自包含，不能 import
业务代码），此后以表为准；表为空时服务层仍回落到代码内置值，兼容旧部署。

纯新增表，SQLite/Postgres 均可直接执行。

Revision ID: 0086_billing_plans
Revises: 0085_billing_payment_fields
"""

import json
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0086_billing_plans"
down_revision = "0085_billing_payment_fields"
branch_labels = None
depends_on = None

_TABLE = "billing_plans"

#: 各套餐的可对比卖点（与 app/modules/billing/plans.py 的 highlights 一致）
_HIGHLIGHTS: dict[str, tuple[str, ...]] = {
    "basic": (
        "单小院日常运营全流程",
        "含 200 万 Token/月 追加额度",
        "含 5,000 次模型调用/月",
        "知识库与工单闭环",
        "超限走审批申请，不中断服务",
    ),
    "pro": (
        "多智能体并发与指挥官自动化",
        "含 600 万 Token/月 追加额度",
        "含 15,000 次模型调用/月",
        "含 ¥200 模型算力额度/月（可跑贵模型）",
        "批量诊断与批量导出",
    ),
    "flagship": (
        "院级全域自动化与多小院协同",
        "含 2,000 万 Token/月 追加额度",
        "含 50,000 次模型调用/月",
        "含 ¥600 模型算力额度/月",
        "大规模知识资产沉淀与监督测评",
    ),
    "booster-tokens": (
        "一次性 100 万 Token",
        "30 天内有效，不占自然月额度",
        "适合临时汇报、批量整理",
    ),
    "booster-cost": (
        "一次性 ¥100 模型算力额度",
        "30 天内有效，不占自然月额度",
        "按上游成本价折算，专治贵模型跑不动",
    ),
}

#: 与 app/modules/billing/plans.py 的 PLANS 保持一致的初始目录
_SEED = (
    (
        "basic",
        "基础版",
        "subscription",
        1999.0,
        "单小院起步：日常对话、知识库与工单闭环",
        10,
        (
            {
                "metric": "tokens",
                "window": "month",
                "amount": 2000000.0,
                "note": "每月追加 200 万 Token",
            },
            {
                "metric": "calls",
                "window": "month",
                "amount": 5000.0,
                "note": "每月追加 5,000 次模型调用",
            },
        ),
    ),
    (
        "pro",
        "专业版",
        "subscription",
        4999.0,
        "多智能体重度使用：指挥官自动化与批量诊断",
        20,
        (
            {
                "metric": "tokens",
                "window": "month",
                "amount": 6000000.0,
                "note": "每月追加 600 万 Token",
            },
            {
                "metric": "calls",
                "window": "month",
                "amount": 15000.0,
                "note": "每月追加 15,000 次模型调用",
            },
            {
                "metric": "cost",
                "window": "month",
                "amount": 200.0,
                "note": "每月追加 ¥200 模型算力额度",
            },
        ),
    ),
    (
        "flagship",
        "旗舰版",
        "subscription",
        9999.0,
        "院级旗舰：全域自动化 + 大规模知识资产沉淀",
        30,
        (
            {
                "metric": "tokens",
                "window": "month",
                "amount": 20000000.0,
                "note": "每月追加 2,000 万 Token",
            },
            {
                "metric": "calls",
                "window": "month",
                "amount": 50000.0,
                "note": "每月追加 50,000 次模型调用",
            },
            {
                "metric": "cost",
                "window": "month",
                "amount": 600.0,
                "note": "每月追加 ¥600 模型算力额度",
            },
        ),
    ),
    (
        "booster-tokens",
        "加油包 · Token",
        "booster",
        299.0,
        "急活专用：一次性 100 万 Token，30 天有效",
        40,
        (
            {
                "metric": "tokens",
                "window": "month",
                "amount": 1000000.0,
                "note": "30 天内有效 100 万 Token",
            },
        ),
    ),
    (
        "booster-cost",
        "加油包 · 算力",
        "booster",
        199.0,
        "贵模型专项：一次性 ¥100 算力额度，30 天有效",
        50,
        (
            {
                "metric": "cost",
                "window": "month",
                "amount": 100.0,
                "note": "30 天内有效 ¥100 模型算力",
            },
        ),
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.String(36), primary_key=True, nullable=False),
            sa.Column("code", sa.String(32), nullable=False, unique=True),
            sa.Column("name", sa.String(64), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False, server_default="subscription"),
            sa.Column("price_yuan", sa.Float(), nullable=False, server_default="0"),
            sa.Column("tagline", sa.String(200), nullable=False, server_default=""),
            sa.Column("items", sa.JSON(), nullable=False),
            sa.Column("highlights", sa.Text(), nullable=True),
            sa.Column("note", sa.String(200), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_billing_plans_active_sort", _TABLE, ["active", "sort_order"], unique=False
        )
    else:
        inspector = sa.inspect(bind)
        if not any(c["name"] == "sort_order" for c in inspector.get_columns(_TABLE)):
            op.add_column(
                _TABLE,
                sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            )
        if not any(c["name"] == "highlights" for c in inspector.get_columns(_TABLE)):
            op.add_column(_TABLE, sa.Column("highlights", sa.Text(), nullable=True))
        if not any(c["name"] == "active" for c in inspector.get_columns(_TABLE)):
            op.add_column(
                _TABLE,
                sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            )

    # 播种：仅在表为空时写入，避免覆盖运营已改过的目录
    count = bind.execute(sa.text(f"SELECT COUNT(*) FROM {_TABLE}")).scalar() or 0
    if count:
        return
    now = datetime.now(UTC)
    for code, name, kind, price, tagline, sort_order, items in _SEED:
        bind.execute(
            sa.text(
                f"INSERT INTO {_TABLE} "
                "(id, code, name, kind, price_yuan, tagline, items, highlights, note,"
                " sort_order, active, created_at, updated_at) "
                "VALUES (:id, :code, :name, :kind, :price, :tagline, :items, :highlights, NULL,"
                " :sort, :active, :now, :now)"
            ),
            {
                "id": f"plan-{code}",
                "highlights": json.dumps(list(_HIGHLIGHTS.get(code, [])), ensure_ascii=False),
                "code": code,
                "name": name,
                "kind": kind,
                "price": price,
                "tagline": tagline,
                # SQLite/Postgres 都接受字符串形式的 JSON 文本
                "items": json.dumps(list(items), ensure_ascii=False),
                "sort": sort_order,
                # 必须是 Python bool 而不是字面量 1：
                # PostgreSQL 的 boolean 列拒绝整数（DatatypeMismatch），
                # SQLite 则宽松接受——写死 1 会让 0086 只在 SQLite 上跑得通，
                # 生产库升级直接失败（v3.80 实测）。
                "active": True,
                "now": now,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table(_TABLE):
        op.drop_table(_TABLE)
