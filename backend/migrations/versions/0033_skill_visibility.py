"""技能归属与可见性：私有 / 小院 / 平台三级（v3.2 一句话生成技能的前置）。

Revision ID: 0033_skill_visibility
Revises: 0032_trace_user
Create Date: 2026-10-27
"""

import sqlalchemy as sa
from alembic import op

revision = "0033_skill_visibility"
down_revision = "0032_trace_user"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "evolution_skills",
        sa.Column("visibility", sa.String(16), nullable=False, server_default="yard"),
    )
    op.add_column("evolution_skills", sa.Column("owner_user_id", sa.String(36), nullable=True))
    op.add_column("evolution_skills", sa.Column("origin", sa.String(32), nullable=True))
    op.add_column("evolution_skills", sa.Column("reviewed_by", sa.String(36), nullable=True))
    op.add_column(
        "evolution_skills", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("evolution_skills", sa.Column("review_note", sa.Text(), nullable=True))
    op.create_index("ix_evolution_skills_visibility", "evolution_skills", ["visibility"])
    op.create_index("ix_evolution_skills_owner_user_id", "evolution_skills", ["owner_user_id"])

    # 存量回填：内置铺垫的技能属于平台级，其余历史技能保持小院级可见（不改变现有行为）
    op.execute(
        "UPDATE evolution_skills SET visibility = 'platform' "
        "WHERE lower(coalesce(created_from, '')) LIKE '%market_builtin%'"
    )
    op.execute(
        "UPDATE evolution_skills SET origin = CASE "
        "WHEN lower(coalesce(created_from, '')) LIKE '%market_builtin%' THEN 'builtin' "
        "WHEN lower(coalesce(created_from, '')) LIKE '%user_generated%' THEN 'user_generated' "
        "WHEN lower(coalesce(created_from, '')) LIKE '%chat%' THEN 'user_generated' "
        "WHEN lower(coalesce(created_from, '')) LIKE '%market%' THEN 'market' "
        "WHEN lower(coalesce(created_from, '')) LIKE '%github%' THEN 'market' "
        "ELSE 'imported' END"
    )


def downgrade() -> None:
    op.drop_index("ix_evolution_skills_owner_user_id", table_name="evolution_skills")
    op.drop_index("ix_evolution_skills_visibility", table_name="evolution_skills")
    op.drop_column("evolution_skills", "review_note")
    op.drop_column("evolution_skills", "reviewed_at")
    op.drop_column("evolution_skills", "reviewed_by")
    op.drop_column("evolution_skills", "origin")
    op.drop_column("evolution_skills", "owner_user_id")
    op.drop_column("evolution_skills", "visibility")
