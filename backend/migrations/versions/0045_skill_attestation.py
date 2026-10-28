"""技能签名与上架前评测报告（v3.17）

本次只加列、不改存量数据：
- evolution_skills：定义摘要 + 签名，发布/推广时写入；存量已发布行为 NULL，
  接口如实返回「未签名」，不静默当作可信。
- eval_runs：kind + 完整报告 JSON + 报告/数据集摘要，审核与安装各留一份。

回滚即删列，不改动既有行内容。

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0045_skill_attestation"
down_revision = "0044_chat_session_view_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "evolution_skills", sa.Column("definition_sha256", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "evolution_skills", sa.Column("signature_alg", sa.String(length=20), nullable=True)
    )
    op.add_column("evolution_skills", sa.Column("signature", sa.Text(), nullable=True))
    op.add_column("evolution_skills", sa.Column("signed_by", sa.String(length=36), nullable=True))
    op.add_column(
        "evolution_skills",
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("evolution_skills", sa.Column("key_id", sa.String(length=32), nullable=True))

    op.add_column(
        "eval_runs",
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="gate"),
    )
    op.add_column("eval_runs", sa.Column("report_json", sa.JSON(), nullable=True))
    op.add_column("eval_runs", sa.Column("report_sha256", sa.String(length=64), nullable=True))
    op.add_column("eval_runs", sa.Column("dataset_sha256", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("eval_runs", "dataset_sha256")
    op.drop_column("eval_runs", "report_sha256")
    op.drop_column("eval_runs", "report_json")
    op.drop_column("eval_runs", "kind")

    op.drop_column("evolution_skills", "key_id")
    op.drop_column("evolution_skills", "signed_at")
    op.drop_column("evolution_skills", "signed_by")
    op.drop_column("evolution_skills", "signature")
    op.drop_column("evolution_skills", "signature_alg")
    op.drop_column("evolution_skills", "definition_sha256")
