"""修复卡在待审核的本机能力/工作区插件（v3.63.1）

这类插件没有工作流审核通道，旧版本安装后永久停在 pending_review。
已有有效授权（consent 未撤销）的，直接置为 enabled；执行时仍会按
scope_hash 复核授权，权限变化后依旧 409。

Revision ID: 0069_enable_runtime_plugins
Revises: 0068_workspace
"""

import sqlalchemy as sa
from alembic import op

revision = "0069_enable_runtime_plugins"
down_revision = "0068_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE plugin_manifests
            SET status = 'enabled', updated_at = CURRENT_TIMESTAMP
            WHERE type IN ('local_runtime', 'workspace')
              AND status = 'pending_review'
              AND EXISTS (
                  SELECT 1 FROM plugin_consents c
                  WHERE c.revoked_at IS NULL
                    AND c.plugin_id = plugin_manifests.plugin_id
                    AND c.yard_id = plugin_manifests.yard_id
              )
            """
        )
    )


def downgrade() -> None:
    # 数据修复不回退：让插件回到不可用状态没有收益
    pass
