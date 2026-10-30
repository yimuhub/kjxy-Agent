"""作物诊断材料与来源（v3.27）

- ``diagnosis_attachments``：现场照片、文档/PDF 先上传后关联诊断（diagnosis_id 可空）；
  存储键随机，读取接口必须带小院校验，不能当公开 URL。
- ``crop_diagnoses.source``/``device_id``：区分人工描述与已对接设备推送后的自动诊断。

纯增量迁移，可完整 downgrade。

Revision ID: 0053_diagnosis_attachments
Revises: 0052_tool_catalog
Create Date: 2026-10-30
"""

import sqlalchemy as sa
from alembic import op

revision = "0053_diagnosis_attachments"
down_revision = "0052_tool_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "diagnosis_attachments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("yard_id", sa.String(length=36), nullable=False),
        sa.Column("diagnosis_id", sa.String(length=36), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("mime", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        # image | document
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_diagnosis_attachments_yard_id", "diagnosis_attachments", ["yard_id"])
    op.create_index(
        "ix_diagnosis_attachments_diagnosis_id", "diagnosis_attachments", ["diagnosis_id"]
    )
    op.create_index(
        "ix_diagnosis_attachments_yard_diag",
        "diagnosis_attachments",
        ["yard_id", "diagnosis_id"],
    )

    op.add_column(
        "crop_diagnoses",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
    )
    op.add_column("crop_diagnoses", sa.Column("device_id", sa.String(length=36), nullable=True))
    op.create_index("ix_crop_diagnoses_device_id", "crop_diagnoses", ["device_id"])


def downgrade() -> None:
    op.drop_index("ix_crop_diagnoses_device_id", table_name="crop_diagnoses")
    op.drop_column("crop_diagnoses", "device_id")
    op.drop_column("crop_diagnoses", "source")

    op.drop_index("ix_diagnosis_attachments_yard_diag", table_name="diagnosis_attachments")
    op.drop_index("ix_diagnosis_attachments_diagnosis_id", table_name="diagnosis_attachments")
    op.drop_index("ix_diagnosis_attachments_yard_id", table_name="diagnosis_attachments")
    op.drop_table("diagnosis_attachments")
