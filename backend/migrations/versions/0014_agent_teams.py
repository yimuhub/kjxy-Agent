"""班组协作：agent_teams / team_runs / team_run_members 三表（P0 班组）。

Revision ID: 0014_agent_teams
Revises: 0013_run_steps_checkpoint
Create Date: 2026-10-21

背景（W2 班组接力）：多 Agent 顺序接力协作——
1. ``agent_teams``：班组定义。members_json 为**有序**成员数组
   [{agent_id, role_name, instruction, is_critic}]（服务层约束 2..8 个）；
   同租户班组名唯一（UniqueConstraint）。
2. ``team_runs``：一次班组运行。status running|completed|failed；
   members_json 是**运行时成员定义快照**（班组事后编辑/停用不影响续跑口径）；
   current_member 指向当前执行位；failed 停在断点，retry 从断点续跑。
3. ``team_run_members``：每个成员一行接力台账。task_id = 该成员声明式
   run 的**预置 run_id**（先落库再执行，崩溃可按 W1 checkpoint 续跑，
   已 ok 的工具副作用不重放）；status pending|running|ok|error。

可逆：downgrade 依序删三表，不动任何既有业务数据行。
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_agent_teams"
down_revision = "0013_run_steps_checkpoint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_teams",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("model_hint", sa.String(100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        # 有序成员数组：[{agent_id, role_name, instruction, is_critic}]（≥2 ≤8，服务层校验）
        sa.Column("members_json", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.UniqueConstraint("tenant_id", "name", name="uq_agent_teams_tenant_name"),
    )
    op.create_index("ix_agent_teams_tenant_id", "agent_teams", ["tenant_id"])

    op.create_table(
        "team_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("team_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("answer", sa.Text(), nullable=True),
        # 运行时成员定义快照：retry/审计口径不随班组事后编辑漂移
        sa.Column("members_json", sa.JSON(), nullable=False),
        sa.Column("current_member", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_team_runs_tenant_id", "team_runs", ["tenant_id"])
    op.create_index("ix_team_runs_team_id", "team_runs", ["team_id"])

    op.create_table(
        "team_run_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("team_run_id", sa.String(36), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("member_agent_id", sa.String(36), nullable=False),
        sa.Column("role_name", sa.String(100), nullable=False),
        # 预置的声明式 run_id：先落库再执行 → 崩溃可按 W1 checkpoint 续跑
        sa.Column("task_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("input_preview", sa.Text(), nullable=True),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
    )
    op.create_index("ix_team_run_members_team_run_id", "team_run_members", ["team_run_id"])


def downgrade() -> None:
    op.drop_index("ix_team_run_members_team_run_id", table_name="team_run_members")
    op.drop_table("team_run_members")
    op.drop_index("ix_team_runs_team_id", table_name="team_runs")
    op.drop_index("ix_team_runs_tenant_id", table_name="team_runs")
    op.drop_table("team_runs")
    op.drop_index("ix_agent_teams_tenant_id", table_name="agent_teams")
    op.drop_table("agent_teams")
