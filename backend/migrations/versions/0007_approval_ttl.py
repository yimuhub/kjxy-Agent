"""工具审批 TTL 与复用上限：tool_approvals 增加 expires_at / used_count。

Revision ID: 0007_approval_ttl
Revises: 0006_llm_suppliers
Create Date: 2026-10-17

背景：规范 §15.2 R3 要求高风险外部副作用操作有二次确认与审计；此前审批单
批准后可无限期、无限次复用。本迁移为审批单增加有效期（expires_at）与
已放行次数（used_count），由 settings.tool_approval_ttl_hours 与
tool_approval_max_uses 控制。
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_approval_ttl"
down_revision = "0006_llm_suppliers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tool_approvals",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tool_approvals",
        sa.Column(
            "used_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    # 复合索引：按 (租户, 工具, 状态, 有效期) 快速定位可复用审批
    op.create_index(
        "ix_tool_approvals_reuse",
        "tool_approvals",
        ["tenant_id", "tool_code", "status", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_tool_approvals_reuse", table_name="tool_approvals")
    op.drop_column("tool_approvals", "used_count")
    op.drop_column("tool_approvals", "expires_at")
