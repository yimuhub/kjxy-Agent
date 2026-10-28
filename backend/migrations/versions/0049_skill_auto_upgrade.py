"""Skill 调用埋点与自动升级（v3.21）

新增三张表：
- ``skill_usage_events``：技能参与回答的调用记录（不含正文）；
- ``skill_auto_upgrade``：按技能的自动升级开关与门槛；
- ``skill_upgrade_runs``：一次升级运行的证据与产出草稿。

自动升级只产出待审核草稿，绝不自动发布。纯增量迁移，可完整 downgrade。

Revision ID: 0049
Revises: 0048
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0049_skill_auto_upgrade"
down_revision = "0048_a2a"
branch_labels = None
depends_on = None


def _base_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "skill_usage_events",
        *[
            sa.Column("yard_id", sa.String(length=36), nullable=False),
            sa.Column("skill_code", sa.String(length=100), nullable=False),
            sa.Column("skill_id", sa.String(length=36), nullable=True),
            sa.Column("source", sa.String(length=20), nullable=False),
            sa.Column("session_id", sa.String(length=64), nullable=True),
            sa.Column("outcome", sa.String(length=10), nullable=False),
        ],
        *_base_columns(),
    )
    op.create_index("ix_skill_usage_events_yard_id", "skill_usage_events", ["yard_id"])
    op.create_index(
        "ix_skill_usage_yard_code_time",
        "skill_usage_events",
        ["yard_id", "skill_code", "created_at"],
    )
    op.create_index("ix_skill_usage_yard_session", "skill_usage_events", ["yard_id", "session_id"])

    op.create_table(
        "skill_auto_upgrade",
        *[
            sa.Column("yard_id", sa.String(length=36), nullable=False),
            sa.Column("skill_code", sa.String(length=100), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("min_calls", sa.Integer(), nullable=False, server_default="10"),
            sa.Column("cooldown_hours", sa.Integer(), nullable=False, server_default="168"),
            sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_status", sa.String(length=20), nullable=True),
            sa.Column("last_error", sa.String(length=300), nullable=True),
            sa.Column("created_by", sa.String(length=36), nullable=True),
        ],
        *_base_columns(),
        sa.UniqueConstraint("yard_id", "skill_code", name="uq_skill_auto_yard_code"),
    )
    op.create_index("ix_skill_auto_upgrade_yard_id", "skill_auto_upgrade", ["yard_id"])

    op.create_table(
        "skill_upgrade_runs",
        *[
            sa.Column("yard_id", sa.String(length=36), nullable=False),
            sa.Column("skill_code", sa.String(length=100), nullable=False),
            sa.Column("trigger", sa.String(length=20), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("evidence_json", sa.JSON(), nullable=True),
            sa.Column("draft_skill_id", sa.String(length=36), nullable=True),
            sa.Column("summary", sa.String(length=1000), nullable=True),
            sa.Column("error", sa.String(length=300), nullable=True),
            sa.Column("actor_id", sa.String(length=36), nullable=True),
        ],
        *_base_columns(),
    )
    op.create_index("ix_skill_upgrade_runs_yard_id", "skill_upgrade_runs", ["yard_id"])
    op.create_index("ix_skill_upgrade_yard_code", "skill_upgrade_runs", ["yard_id", "skill_code"])


def downgrade() -> None:
    op.drop_index("ix_skill_upgrade_yard_code", table_name="skill_upgrade_runs")
    op.drop_index("ix_skill_upgrade_runs_yard_id", table_name="skill_upgrade_runs")
    op.drop_table("skill_upgrade_runs")

    op.drop_index("ix_skill_auto_upgrade_yard_id", table_name="skill_auto_upgrade")
    op.drop_table("skill_auto_upgrade")

    op.drop_index("ix_skill_usage_yard_session", table_name="skill_usage_events")
    op.drop_index("ix_skill_usage_yard_code_time", table_name="skill_usage_events")
    op.drop_index("ix_skill_usage_events_yard_id", table_name="skill_usage_events")
    op.drop_table("skill_usage_events")
