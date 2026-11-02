"""统一记忆底座与指挥官预算快照（v3.43 P0）

- ``agent_memories``：情景/摘要/偏好/事实的统一存储，带 scope/visibility 边界；
- ``commander_runs.token_budget``：运行创建时的预算快照，避免设置后续变更影响在途运行；
- ``commander_runs.budget_status``：ok|warning|exceeded，供界面直接展示。

纯增量迁移，可完整 downgrade。
"""

import sqlalchemy as sa
from alembic import op

revision = "0060_commander_memory"
down_revision = "0059_commander_autonomy"
branch_labels = None
depends_on = None


def _embedding_column():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        from pgvector.sqlalchemy import Vector

        return sa.Column("embedding", Vector(1536), nullable=True)
    return sa.Column("embedding", sa.JSON(), nullable=True)


def upgrade() -> None:
    op.create_table(
        "agent_memories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("scope_type", sa.String(16), nullable=False, server_default="yard"),
        sa.Column("scope_key", sa.String(100), nullable=False, server_default=""),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("tags_json", sa.JSON(), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("source_type", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("source_id", sa.String(64), nullable=True),
        sa.Column("visibility", sa.String(16), nullable=False, server_default="private"),
        _embedding_column(),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.UniqueConstraint(
            "yard_id", "kind", "source_type", "source_id", name="uq_agent_memories_source"
        ),
    )
    op.create_index("ix_agent_memories_yard_id", "agent_memories", ["yard_id"])
    op.create_index("ix_agent_memories_kind", "agent_memories", ["kind"])
    op.create_index("ix_agent_memories_source_type", "agent_memories", ["source_type"])
    op.create_index("ix_agent_memories_visibility", "agent_memories", ["visibility"])
    op.create_index("ix_agent_memories_status", "agent_memories", ["status"])
    op.create_index(
        "ix_agent_memories_scope",
        "agent_memories",
        ["yard_id", "scope_type", "scope_key", "kind"],
    )
    op.create_index(
        "ix_agent_memories_created", "agent_memories", ["yard_id", "created_at"]
    )

    with op.batch_alter_table("commander_runs") as batch:
        batch.add_column(
            sa.Column("token_budget", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("budget_status", sa.String(12), nullable=False, server_default="")
        )


def downgrade() -> None:
    with op.batch_alter_table("commander_runs") as batch:
        batch.drop_column("budget_status")
        batch.drop_column("token_budget")
    op.drop_index("ix_agent_memories_created", table_name="agent_memories")
    op.drop_index("ix_agent_memories_scope", table_name="agent_memories")
    op.drop_index("ix_agent_memories_status", table_name="agent_memories")
    op.drop_index("ix_agent_memories_visibility", table_name="agent_memories")
    op.drop_index("ix_agent_memories_source_type", table_name="agent_memories")
    op.drop_index("ix_agent_memories_kind", table_name="agent_memories")
    op.drop_index("ix_agent_memories_yard_id", table_name="agent_memories")
    op.drop_table("agent_memories")
