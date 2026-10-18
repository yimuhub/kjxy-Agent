"""Agent 工厂：factory_runs 表（M3）。

Revision ID: 0010_factory_runs
Revises: 0009_tenant_agents
Create Date: 2026-10-18

背景：给一段行业描述（industry_brief）→ 自动生成一组互补的声明式 Agent
蓝图（LLM 产 JSON，解析失败落内置启发式兜底，generated_by 诚实区分）→
人工挑选采纳（apply）→ 按 ADR-0005 分级发布。

生命周期：claimed（已创建待领取）→ planning（蓝图规划中）→
generating（探测知识覆盖 + 落草稿中）→ completed | failed（error 记录原因）。
blueprint_json 存完整蓝图（含每项 agent_id、knowledge_coverage、
needs_knowledge、smoke_questions），apply 动作记 approved_by。
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_factory_runs"
down_revision = "0009_tenant_agents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "factory_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        # 行业描述（必填）：工厂蓝图的唯一输入
        sa.Column("industry_brief", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(30),
            # claimed|planning|generating|completed|failed
            nullable=False,
            server_default="claimed",
        ),
        # 蓝图 JSON：LLM 或启发式产物（含 coverage / needs_knowledge / agent_id 回写）
        sa.Column("blueprint_json", sa.JSON(), nullable=True),
        # llm|heuristic：蓝图来源诚实标注（FakeLLM/解析失败必须落 heuristic）
        sa.Column("generated_by", sa.String(20), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        # 人工采纳（apply + publish）的操作人
        sa.Column("approved_by", sa.String(36), nullable=True),
    )
    op.create_index("ix_factory_runs_tenant_id", "factory_runs", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_factory_runs_tenant_id", table_name="factory_runs")
    op.drop_table("factory_runs")
