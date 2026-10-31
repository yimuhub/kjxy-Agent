"""官方技能库/插件库：签名清单、版本化包、同步、更新、回滚（v3.35）

- ``library_sources``：官方（yard_id 为空）或小院私有源，Ed25519 发布者公钥；
- ``library_items``：源内条目元数据（同一 item_key 可有多版本）；
- ``library_installs``：小院级安装台账（去重键 yard_id + item_key）；
- ``library_sync_runs``：每次同步的机器可回放记录。

纯增量迁移，可完整 downgrade；不修改既有表。

Revision ID: 0057_official_library
Revises: 0056_supplier_models_relay
Create Date: 2026-11-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0057_official_library"
down_revision = "0056_supplier_models_relay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "library_sources",
        sa.Column("id", sa.String(length=36), primary_key=True),
        # yard_id 为空 = 平台官方源；非空 = 该小院私有源
        sa.Column("yard_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        # official | private
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="official"),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        # stable | beta
        sa.Column("channel", sa.String(length=16), nullable=False, server_default="stable"),
        sa.Column("public_key_b64", sa.Text(), nullable=False, server_default=""),
        sa.Column("key_id", sa.String(length=32), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("etag", sa.String(length=200), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        # unconfigured | ok | error
        sa.Column("status", sa.String(length=20), nullable=False, server_default="unconfigured"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_library_sources_yard_id", "library_sources", ["yard_id"])
    op.create_index("ix_library_sources_kind", "library_sources", ["kind"])

    op.create_table(
        "library_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "source_id",
            sa.String(length=36),
            sa.ForeignKey("library_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_key", sa.String(length=220), nullable=False),
        # skill | plugin | agent_template | deck_theme | doc_template
        sa.Column("item_type", sa.String(length=24), nullable=False),
        sa.Column("code", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("min_app_version", sa.String(length=20), nullable=True),
        sa.Column("max_app_version", sa.String(length=20), nullable=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=40), nullable=True),
        sa.Column("risk_level", sa.String(length=16), nullable=False, server_default="low"),
        sa.Column("license", sa.String(length=50), nullable=True),
        sa.Column("author", sa.String(length=120), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("download_url", sa.String(length=500), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=True),
        # available | installed | blocked | revoked
        sa.Column("status", sa.String(length=20), nullable=False, server_default="available"),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source_id", "item_key", "version", name="uq_library_items_source_item_version"
        ),
    )
    op.create_index("ix_library_items_item_key", "library_items", ["item_key"])
    op.create_index("ix_library_items_status", "library_items", ["status"])

    op.create_table(
        "library_installs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("yard_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("item_key", sa.String(length=220), nullable=False),
        sa.Column("item_type", sa.String(length=24), nullable=False),
        sa.Column("installed_version", sa.String(length=50), nullable=False),
        sa.Column("previous_version", sa.String(length=50), nullable=True),
        sa.Column("package_sha256", sa.String(length=64), nullable=False),
        sa.Column("skill_id", sa.String(length=36), nullable=True),
        sa.Column("plugin_id", sa.String(length=120), nullable=True),
        sa.Column("auto_update", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("yard_id", "item_key", name="uq_library_installs_yard_item"),
    )
    op.create_index("ix_library_installs_yard_id", "library_installs", ["yard_id"])

    op.create_table(
        "library_sync_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("yard_id", sa.String(length=36), nullable=True),
        # ok | error | skipped
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("added", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("removed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_library_sync_runs_source_id", "library_sync_runs", ["source_id"])


def downgrade() -> None:
    op.drop_index("ix_library_sync_runs_source_id", table_name="library_sync_runs")
    op.drop_table("library_sync_runs")

    op.drop_index("ix_library_installs_yard_id", table_name="library_installs")
    op.drop_table("library_installs")

    op.drop_index("ix_library_items_status", table_name="library_items")
    op.drop_index("ix_library_items_item_key", table_name="library_items")
    op.drop_table("library_items")

    op.drop_index("ix_library_sources_kind", table_name="library_sources")
    op.drop_index("ix_library_sources_yard_id", table_name="library_sources")
    op.drop_table("library_sources")
