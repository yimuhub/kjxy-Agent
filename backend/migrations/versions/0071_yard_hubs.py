"""0071 yard_hubs/hub_members + yards.hub_id：枢纽层级与运营模式（v3.65）。"""

import sqlalchemy as sa
from alembic import op

revision = "0071_yard_hubs"
down_revision = "0070_yard_journal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "yard_hubs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("mode", sa.String(12), nullable=False),
        sa.Column("region", sa.String(120), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "hub_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("hub_id", sa.String(36), nullable=False, index=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(12), nullable=False, server_default="viewer"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("hub_id", "user_id", name="uq_hub_members_hub_user"),
    )
    op.add_column("yards", sa.Column("hub_id", sa.String(36), nullable=True))
    op.create_index("ix_yards_hub", "yards", ["hub_id"])


def downgrade() -> None:
    op.drop_index("ix_yards_hub", table_name="yards")
    op.drop_column("yards", "hub_id")
    op.drop_table("hub_members")
    op.drop_table("yard_hubs")
