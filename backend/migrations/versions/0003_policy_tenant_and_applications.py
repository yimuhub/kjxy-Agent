"""政策模块地基：policies 加 tenant_id；新建 policy_applications 表（第三期 3.1）。

Revision ID: 0003_policy_tenant_and_applications
Revises: 0002_tool_approvals
Create Date: 2026-10-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_policy_tenant_and_applications"
down_revision = "0002_tool_approvals"
branch_labels = None
depends_on = None


def _ts():
    return [
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
    ]


def upgrade() -> None:
    # 兼容修正：0001 创建的 alembic_version.version_num 为 VARCHAR(32)，装不下本
    # revision 名（36 字符）；PostgreSQL 强类型会在升级完成后的版本落库
    # UPDATE 时报 StringDataRightTruncation（CI 首跑已实证）。此处先行放宽列宽，
    # 仅 PG 需要；SQLite 的 varchar 长度不强制，跳过。
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(100)"
        )
    # policies 加 tenant_id（SQLite 下用 batch 模式；nullable 以兼容老数据）
    with op.batch_alter_table("policies", recreate="auto") as batch_op:
        batch_op.add_column(sa.Column("tenant_id", sa.String(36), nullable=True))
        batch_op.create_index("ix_policies_tenant_id", ["tenant_id"])

    # 政策申报申请单
    op.create_table(
        "policy_applications",
        *_ts(),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("policy_id", sa.String(36), nullable=False),
        sa.Column("applicant_name", sa.String(100), nullable=False),
        # 个人 | 家庭农场 | 合作社 | 企业
        sa.Column("applicant_type", sa.String(50), nullable=True),
        sa.Column("contact_info", sa.JSON, nullable=True),
        # 填充的申请表字段
        sa.Column("form_data", sa.JSON, nullable=True),
        # 材料清单
        sa.Column("material_checklist", sa.JSON, nullable=True),
        # 缺失材料
        sa.Column("missing_materials", sa.JSON, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        # draft | submitted
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(
            ["policy_id"], ["policies.id"], name="fk_policy_applications_policy_id"
        ),
    )
    op.create_index("ix_policy_applications_tenant_id", "policy_applications", ["tenant_id"])
    op.create_index("ix_policy_applications_policy_id", "policy_applications", ["policy_id"])
    op.create_index("ix_policy_applications_status", "policy_applications", ["status"])


def downgrade() -> None:
    op.drop_table("policy_applications")
    with op.batch_alter_table("policies", recreate="auto") as batch_op:
        batch_op.drop_index("ix_policies_tenant_id")
        batch_op.drop_column("tenant_id")
