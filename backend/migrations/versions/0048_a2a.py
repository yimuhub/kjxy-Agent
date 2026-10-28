"""A2A 互通（v3.20）

新增三张表：
- ``a2a_endpoints``：小院入站协作入口，令牌只存 sha256，展示前缀；
- ``a2a_peers``：出站协作方（根地址、Fernet 加密令牌、Agent Card 快照）；
- ``a2a_tasks``：入站/出站任务，(yard_id, op_key) 唯一用于幂等。

纯增量迁移；回滚即删三张表，不影响既有数据。

Revision ID: 0048
Revises: 0047
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0048_a2a"
down_revision = "0047_offline_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "a2a_endpoints",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("yard_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_prefix", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("yard_id", name="uq_a2a_endpoints_yard"),
    )
    op.create_index("ix_a2a_endpoints_token_hash", "a2a_endpoints", ["token_hash"])

    op.create_table(
        "a2a_peers",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("yard_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("token_enc", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("card_json", sa.JSON(), nullable=True),
        sa.Column("card_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("yard_id", "name", name="uq_a2a_peers_yard_name"),
    )
    op.create_index("ix_a2a_peers_yard_id", "a2a_peers", ["yard_id"])

    op.create_table(
        "a2a_tasks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("yard_id", sa.String(length=36), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("peer_id", sa.String(length=36), nullable=True),
        sa.Column("skill_id", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("error_message", sa.String(length=300), nullable=True),
        sa.Column("remote_task_id", sa.String(length=64), nullable=True),
        sa.Column("context_id", sa.String(length=64), nullable=True),
        sa.Column("op_key", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("yard_id", "op_key", name="uq_a2a_tasks_yard_op"),
    )
    op.create_index("ix_a2a_tasks_yard_id", "a2a_tasks", ["yard_id"])
    op.create_index("ix_a2a_tasks_peer_id", "a2a_tasks", ["peer_id"])
    op.create_index("ix_a2a_tasks_yard_created", "a2a_tasks", ["yard_id", "created_at"])
    op.create_index("ix_a2a_tasks_yard_direction", "a2a_tasks", ["yard_id", "direction"])


def downgrade() -> None:
    op.drop_index("ix_a2a_tasks_yard_direction", table_name="a2a_tasks")
    op.drop_index("ix_a2a_tasks_yard_created", table_name="a2a_tasks")
    op.drop_index("ix_a2a_tasks_peer_id", table_name="a2a_tasks")
    op.drop_index("ix_a2a_tasks_yard_id", table_name="a2a_tasks")
    op.drop_table("a2a_tasks")

    op.drop_index("ix_a2a_peers_yard_id", table_name="a2a_peers")
    op.drop_table("a2a_peers")

    op.drop_index("ix_a2a_endpoints_token_hash", table_name="a2a_endpoints")
    op.drop_table("a2a_endpoints")
