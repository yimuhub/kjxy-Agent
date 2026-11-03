"""工作区插件（v3.63）：项目根与文件变更台账。

变更同时保存修改前后的完整文本快照（单文件 1MB 上限），
回滚按快照原子写回或删除。
"""

import sqlalchemy as sa
from alembic import op

revision = "0068_workspace"
down_revision = "0067_plugin_consents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("root_path", sa.String(1024), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("allowed_paths", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("updated_by", sa.String(36), nullable=True),
        sa.UniqueConstraint("yard_id", "name", name="uq_workspace_projects_yard_name"),
    )
    op.create_index("ix_workspace_projects_yard_id", "workspace_projects", ["yard_id"])
    op.create_index(
        "ix_workspace_projects_yard_status", "workspace_projects", ["yard_id", "status"]
    )

    op.create_table(
        "workspace_changes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("plugin_id", sa.String(120), nullable=False, server_default=""),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="preview"),
        sa.Column("before_existed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("before_content", sa.Text(), nullable=True),
        sa.Column("before_sha", sa.String(64), nullable=True),
        sa.Column("after_content", sa.Text(), nullable=False, server_default=""),
        sa.Column("after_sha", sa.String(64), nullable=False),
        sa.Column("diff_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("diff_truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("added_lines", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("removed_lines", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("git_head_before", sa.String(64), nullable=True),
        sa.Column("git_head_after", sa.String(64), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("applied_by", sa.String(36), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_by", sa.String(36), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_workspace_changes_yard_id", "workspace_changes", ["yard_id"])
    op.create_index(
        "ix_workspace_changes_yard_project",
        "workspace_changes",
        ["yard_id", "project_id"],
    )
    op.create_index(
        "ix_workspace_changes_yard_status", "workspace_changes", ["yard_id", "status"]
    )
    op.create_index("ix_workspace_changes_project_id", "workspace_changes", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_workspace_changes_project_id", table_name="workspace_changes")
    op.drop_index("ix_workspace_changes_yard_status", table_name="workspace_changes")
    op.drop_index("ix_workspace_changes_yard_project", table_name="workspace_changes")
    op.drop_index("ix_workspace_changes_yard_id", table_name="workspace_changes")
    op.drop_table("workspace_changes")
    op.drop_index("ix_workspace_projects_yard_status", table_name="workspace_projects")
    op.drop_index("ix_workspace_projects_yard_id", table_name="workspace_projects")
    op.drop_table("workspace_projects")
