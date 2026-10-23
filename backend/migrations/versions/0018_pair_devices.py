"""手机配对：``pair_devices`` / ``pair_codes``（扫码接入局域网，Wave B）。

Revision ID: 0018_pair_devices
Revises: 0017_chat_sessions
Create Date: 2026-10-23

背景：手机 App 要与局域网内的平台配对。管理员生成一次性配对码
（``pair_codes``，只存 sha256、TTL 5 分钟、用过即废），手机扫码后到公开
交换端点换取 30 天设备令牌并建档（``pair_devices``，只存令牌哈希，
可吊销可过期）。

可逆：downgrade 删两表，不动任何既有业务数据行。
"""

import sqlalchemy as sa
from alembic import op

revision = "0018_pair_devices"
down_revision = "0017_chat_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pair_devices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        # 扫码交换无登录主体：诚实置空（区别于 Web 端创建的资产）
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("name", sa.String(100), nullable=False),
        # token 只存 sha256：泄露库不等于泄露令牌
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_pair_devices_tenant_id", "pair_devices", ["tenant_id"])
    op.create_index("ix_pair_devices_created_by", "pair_devices", ["created_by"])

    op.create_table(
        "pair_codes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        # 配对码只存哈希：审计/备份泄露都不等于配对能力泄露
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        # 交换成功打标；未打标且未过期才算"未用码"（配额统计口径）
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_pair_codes_tenant_id", "pair_codes", ["tenant_id"])
    op.create_index("ix_pair_codes_code_hash", "pair_codes", ["code_hash"])


def downgrade() -> None:
    op.drop_index("ix_pair_codes_code_hash", table_name="pair_codes")
    op.drop_index("ix_pair_codes_tenant_id", table_name="pair_codes")
    op.drop_table("pair_codes")
    op.drop_index("ix_pair_devices_created_by", table_name="pair_devices")
    op.drop_index("ix_pair_devices_tenant_id", table_name="pair_devices")
    op.drop_table("pair_devices")
