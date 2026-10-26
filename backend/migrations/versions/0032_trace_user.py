"""模型调用追踪补充用户/会话归属（负责人驾驶舱按人汇总）。

Revision ID: 0032_trace_user
Revises: 0031_market_items
Create Date: 2026-10-27
"""

import sqlalchemy as sa
from alembic import op

revision = "0032_trace_user"
down_revision = "0031_market_items"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("llm_traces", sa.Column("user_id", sa.String(36), nullable=True))
    op.add_column("llm_traces", sa.Column("session_id", sa.String(36), nullable=True))
    op.create_index("ix_llm_traces_user_id", "llm_traces", ["user_id"], unique=False)
    op.create_index("ix_llm_traces_session_id", "llm_traces", ["session_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_llm_traces_session_id", table_name="llm_traces")
    op.drop_index("ix_llm_traces_user_id", table_name="llm_traces")
    op.drop_column("llm_traces", "session_id")
    op.drop_column("llm_traces", "user_id")
