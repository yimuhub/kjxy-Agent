"""插件权限授权台账（v3.59）

权限集合指纹变化即失效，需重新确认；撤销保留历史。
"""

import sqlalchemy as sa
from alembic import op

revision = "0067_plugin_consents"
down_revision = "0066_org_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plugin_consents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("item_key", sa.String(220), nullable=False),
        sa.Column("plugin_id", sa.String(120), nullable=False),
        sa.Column("plugin_version", sa.String(50), nullable=False),
        sa.Column("scope_hash", sa.String(64), nullable=False),
        sa.Column("permissions_json", sa.JSON(), nullable=False),
        sa.Column("risk_level", sa.String(16), nullable=False, server_default="low"),
        sa.Column("ack_version", sa.String(32), nullable=False, server_default="v1"),
        sa.Column("accepted_by", sa.String(36), nullable=True),
        sa.Column("accepted_by_name", sa.String(120), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(36), nullable=True),
        sa.UniqueConstraint(
            "yard_id", "item_key", "scope_hash", name="uq_plugin_consents_yard_item_scope"
        ),
    )
    op.create_index("ix_plugin_consents_yard_id", "plugin_consents", ["yard_id"])
    op.create_index("ix_plugin_consents_item_key", "plugin_consents", ["item_key"])


def downgrade() -> None:
    op.drop_index("ix_plugin_consents_item_key", table_name="plugin_consents")
    op.drop_index("ix_plugin_consents_yard_id", table_name="plugin_consents")
    op.drop_table("plugin_consents")
