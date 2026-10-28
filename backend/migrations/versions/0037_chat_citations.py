"""chat_messages.citations_json（v3.7 可信引用）

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0037_chat_citations"
down_revision = "0036_team_runs_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("citations_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_messages", "citations_json")
