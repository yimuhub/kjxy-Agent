"""定时任务底座与自我总结记忆（v3.4）。

Revision ID: 0034_yard_jobs
Revises: 0033_skill_visibility
Create Date: 2026-10-27
"""

import sqlalchemy as sa
from alembic import op

revision = "0034_yard_jobs"
down_revision = "0033_skill_visibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "yard_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("schedule_json", sa.JSON(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("yard_id", "kind", name="uq_yard_jobs_yard_kind"),
    )
    op.create_index("ix_yard_jobs_yard_id", "yard_jobs", ["yard_id"], unique=False)
    op.create_index("ix_yard_jobs_next_run_at", "yard_jobs", ["next_run_at"], unique=False)
    op.create_index("ix_yard_jobs_due", "yard_jobs", ["status", "next_run_at"], unique=False)

    op.create_table(
        "yard_job_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("trigger", sa.String(16), nullable=False, server_default="schedule"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(36), nullable=True),
        sa.Column("actor_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_yard_job_runs_yard_id", "yard_job_runs", ["yard_id"], unique=False)
    op.create_index("ix_yard_job_runs_job_id", "yard_job_runs", ["job_id"], unique=False)
    op.create_index(
        "ix_yard_job_runs_yard_created", "yard_job_runs", ["yard_id", "created_at"], unique=False
    )

    op.create_table(
        "self_improve_memories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("tags_json", sa.JSON(), nullable=False),
        sa.Column("window_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_run_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_self_improve_memories_yard_id", "self_improve_memories", ["yard_id"], unique=False
    )
    op.create_index(
        "ix_self_improve_memories_user_id", "self_improve_memories", ["user_id"], unique=False
    )
    op.create_index(
        "ix_self_improve_memories_yard_user",
        "self_improve_memories",
        ["yard_id", "user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_self_improve_memories_yard_user", table_name="self_improve_memories")
    op.drop_index("ix_self_improve_memories_user_id", table_name="self_improve_memories")
    op.drop_index("ix_self_improve_memories_yard_id", table_name="self_improve_memories")
    op.drop_table("self_improve_memories")

    op.drop_index("ix_yard_job_runs_yard_created", table_name="yard_job_runs")
    op.drop_index("ix_yard_job_runs_job_id", table_name="yard_job_runs")
    op.drop_index("ix_yard_job_runs_yard_id", table_name="yard_job_runs")
    op.drop_table("yard_job_runs")

    op.drop_index("ix_yard_jobs_due", table_name="yard_jobs")
    op.drop_index("ix_yard_jobs_next_run_at", table_name="yard_jobs")
    op.drop_index("ix_yard_jobs_yard_id", table_name="yard_jobs")
    op.drop_table("yard_jobs")
