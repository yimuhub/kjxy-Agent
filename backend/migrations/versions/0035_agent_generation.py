"""一句话生成智能体：蓝图与待补技能（v3.5）。

生成草稿保留「为什么这么设计」的痕迹：hex/框架六段/参考来源（blueprint_json），
以及模型认为需要但平台暂缺的技能（skill_requirements）。两列仅生成草稿非空，
手工创建/工厂产出的行保持 NULL，读取方一律按空处理。

Revision ID: 0035_agent_generation
Revises: 0034_yard_jobs
Create Date: 2026-10-27
"""

import sqlalchemy as sa
from alembic import op

revision = "0035_agent_generation"
down_revision = "0034_yard_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("yard_agents", sa.Column("blueprint_json", sa.JSON(), nullable=True))
    op.add_column("yard_agents", sa.Column("skill_requirements", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("yard_agents", "skill_requirements")
    op.drop_column("yard_agents", "blueprint_json")
