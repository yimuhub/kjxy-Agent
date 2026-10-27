"""班组异步运行、研究文档与间隔计划。"""

import sqlalchemy as sa
from alembic import op

revision = "0036_team_runs_schedule"
down_revision = "0035_agent_generation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_teams", sa.Column("schedule_json", sa.JSON(), nullable=True))
    op.add_column("agent_teams", sa.Column("schedule_created_by", sa.String(36), nullable=True))
    op.add_column(
        "agent_teams", sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_agent_teams_next_run_at", "agent_teams", ["next_run_at"])
    for column in (
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("team_name", sa.String(100), nullable=True),
        sa.Column("trigger", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("output_format", sa.String(8), nullable=False, server_default="text"),
        sa.Column("research_json", sa.JSON(), nullable=True),
        sa.Column("document_json", sa.JSON(), nullable=True),
        sa.Column("task_id", sa.String(36), nullable=True),
    ):
        op.add_column("team_runs", column)
    op.execute(
        sa.text(
            "UPDATE team_runs SET team_name = "
            "(SELECT name FROM agent_teams WHERE agent_teams.id = team_runs.team_id)"
        )
    )


def downgrade() -> None:
    for name in (
        "task_id",
        "document_json",
        "research_json",
        "output_format",
        "trigger",
        "team_name",
        "created_by",
    ):
        op.drop_column("team_runs", name)
    op.drop_index("ix_agent_teams_next_run_at", table_name="agent_teams")
    for name in ("next_run_at", "schedule_created_by", "schedule_json"):
        op.drop_column("agent_teams", name)
