"""声明式 Agent：tenant_agents 表（M2 / ADR-0005）。

Revision ID: 0009_tenant_agents
Revises: 0008_pgvector_index
Create Date: 2026-10-18

背景：tenant_admin 需要"配置即 Agent"——不发版、不加代码，在数据库里
声明 persona/任务模板/工具白名单/知识范围即可创建可运行的 Agent。
发布按"风险×归属"分级（ADR-0005）：低风险 tenant_admin 自批；
高风险自动进平台审核队列，platform_admin 批准后才 published。

版本化：同租户内 (tenant_id, agent_code, version) 唯一，可回滚旧版本。
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_tenant_agents"
down_revision = "0008_pgvector_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_agents",
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
        sa.Column("agent_code", sa.String(100), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("emoji", sa.String(32), nullable=False, server_default=""),
        sa.Column("persona_prompt", sa.Text(), nullable=False),
        # 必须含 {{question}} 占位（服务端校验），运行时替换为用户问题
        sa.Column("task_prompt_template", sa.Text(), nullable=False),
        sa.Column("allowed_tools", sa.JSON(), nullable=False),
        sa.Column("knowledge_scopes", sa.JSON(), nullable=False),
        # 可选：指定本租户供应商；空则走现有租户激活供应商解析链
        sa.Column("supplier_id", sa.String(36), nullable=True),
        sa.Column("risk_level", sa.String(10), nullable=False, server_default="low"),
        sa.Column(
            "status",
            sa.String(35),
            # draft|pending_platform_review|published|deprecated
            nullable=False,
            server_default="draft",
        ),
        sa.Column("version", sa.String(50), nullable=False, server_default="1.0.0"),
        sa.Column(
            "created_from",
            sa.String(20),
            # manual|factory|template（工厂/模板生成物默认 draft，红线不因自动而放松）
            nullable=False,
            server_default="manual",
        ),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("updated_by", sa.String(36), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "agent_code", "version", name="uq_tenant_agents_tenant_code_version"
        ),
    )
    op.create_index("ix_tenant_agents_tenant_id", "tenant_agents", ["tenant_id"])
    # 平台审核队列：跨租户按 status 过滤
    op.create_index("ix_tenant_agents_status", "tenant_agents", ["status"])


def downgrade() -> None:
    op.drop_index("ix_tenant_agents_status", table_name="tenant_agents")
    op.drop_index("ix_tenant_agents_tenant_id", table_name="tenant_agents")
    op.drop_table("tenant_agents")
