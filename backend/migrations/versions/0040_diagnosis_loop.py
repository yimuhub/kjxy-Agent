"""作物诊断闭环：诊断记录、跟进、药剂登记（v3.9）

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0040_diagnosis_loop"
down_revision = "0039_farm_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crop_diagnoses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("plot_id", sa.String(36), nullable=True),
        sa.Column("crop", sa.String(100), nullable=False),
        sa.Column("variety", sa.String(100), nullable=False),
        sa.Column("growth_stage", sa.String(100), nullable=False),
        sa.Column("region", sa.String(100), nullable=False),
        sa.Column("symptoms", sa.Text(), nullable=False),
        sa.Column("image_refs", sa.JSON(), nullable=True),
        sa.Column("environment_json", sa.JSON(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("needs_expert_review", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("vision_status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("expert_reviewer_id", sa.String(36), nullable=True),
        sa.Column("expert_note", sa.Text(), nullable=False),
        sa.Column("final_diagnosis", sa.Text(), nullable=False),
        sa.Column("expert_reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_crop_diagnoses_yard_id", "crop_diagnoses", ["yard_id"])
    op.create_index("ix_crop_diagnoses_yard_created", "crop_diagnoses", ["yard_id", "created_at"])
    op.create_index("ix_crop_diagnoses_yard_status", "crop_diagnoses", ["yard_id", "status"])

    op.create_table(
        "diagnosis_followups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("diagnosis_id", sa.String(36), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("image_refs", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
    )
    op.create_index("ix_diagnosis_followups_yard_id", "diagnosis_followups", ["yard_id"])
    op.create_index("ix_diagnosis_followups_diagnosis_id", "diagnosis_followups", ["diagnosis_id"])
    op.create_index(
        "ix_diagnosis_followups_yard_diag",
        "diagnosis_followups",
        ["yard_id", "diagnosis_id"],
    )

    op.create_table(
        "pesticide_products",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("active_ingredient", sa.String(120), nullable=False),
        sa.Column("registered_crops", sa.JSON(), nullable=True),
        sa.Column("pre_harvest_interval_days", sa.Integer(), nullable=False),
        sa.Column("label_notes", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.UniqueConstraint("yard_id", "name", name="uq_pesticide_products_yard_name"),
    )
    op.create_index("ix_pesticide_products_yard_id", "pesticide_products", ["yard_id"])


def downgrade() -> None:
    op.drop_index("ix_pesticide_products_yard_id", table_name="pesticide_products")
    op.drop_table("pesticide_products")
    op.drop_index("ix_diagnosis_followups_yard_diag", table_name="diagnosis_followups")
    op.drop_index("ix_diagnosis_followups_diagnosis_id", table_name="diagnosis_followups")
    op.drop_index("ix_diagnosis_followups_yard_id", table_name="diagnosis_followups")
    op.drop_table("diagnosis_followups")
    op.drop_index("ix_crop_diagnoses_yard_status", table_name="crop_diagnoses")
    op.drop_index("ix_crop_diagnoses_yard_created", table_name="crop_diagnoses")
    op.drop_index("ix_crop_diagnoses_yard_id", table_name="crop_diagnoses")
    op.drop_table("crop_diagnoses")
