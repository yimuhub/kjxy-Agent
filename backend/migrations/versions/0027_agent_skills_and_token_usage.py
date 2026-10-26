"""自建智能体多模型/多技能扩展与对话消息 Token 统计列。

Revision ID: 0027_agent_skills_and_token_usage
Revises: 0026_yard_agent_cover_image
Create Date: 2026-10-26

新增列：
- ``yard_agents.bound_skills``：JSON 数组，记录绑定的多个 Skill / 插件编码；
- ``yard_agents.model_name``：String(100)，可选覆写供应商下的具体子模型名；
- ``chat_messages.token_usage_json``：JSON 对象，记录本条消息的 Token 统计与模型信息。
"""

import sqlalchemy as sa
from alembic import op

revision = "0027_agent_skills_and_token_usage"
down_revision = "0026_yard_agent_cover_image"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "yard_agents",
        sa.Column("bound_skills", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "yard_agents",
        sa.Column("model_name", sa.String(100), nullable=True),
    )
    op.add_column(
        "chat_messages",
        sa.Column("token_usage_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "token_usage_json")
    op.drop_column("yard_agents", "model_name")
    op.drop_column("yard_agents", "bound_skills")
