"""0070 yard_journal：工作日志/月报（角色工作台 P0）。"""

import sqlalchemy as sa
from alembic import op

revision = "0070_yard_journal"
down_revision = "0069_enable_runtime_plugins"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "yard_journal",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("yard_id", sa.String(36), nullable=False, index=True),
        sa.Column("author_user_id", sa.String(36), nullable=False, index=True),
        sa.Column("kind", sa.String(10), nullable=False, server_default="daily"),
        sa.Column("period_key", sa.String(10), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=True),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("content_md", sa.Text(), nullable=False, server_default=""),
        sa.Column("sources_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="draft"),
        sa.Column("reviewed_by", sa.String(36), nullable=True),
        sa.Column("review_note", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "yard_id",
            "author_user_id",
            "kind",
            "period_key",
            name="uq_journal_yard_author_kind_period",
        ),
    )
    op.create_index(
        "ix_journal_yard_period",
        "yard_journal",
        ["yard_id", "kind", "period_key"],
    )
    op.create_index("ix_journal_author", "yard_journal", ["author_user_id"])


def downgrade() -> None:
    op.drop_index("ix_journal_author", table_name="yard_journal")
    op.drop_index("ix_journal_yard_period", table_name="yard_journal")
    op.drop_table("yard_journal")
