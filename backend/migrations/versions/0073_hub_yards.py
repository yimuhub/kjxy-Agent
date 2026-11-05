"""0073 hub_yards：枢纽↔小院多对多关系（v3.67）。

现状：``yards.hub_id`` 单归属，``attach_yard`` 遇到已归属就 409。但现实里一个院
可能同时被判给省科协评审、又被实验站日常管理。改为关联表承载多口径关系，
``yards.hub_id`` 保留为**主归属**（manage 口径），本迁移把老数据幂等回填。
"""

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0073_hub_yards"
down_revision = "0072_yard_attributes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hub_yards",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("hub_id", sa.String(36), nullable=False, index=True),
        sa.Column("yard_id", sa.String(36), nullable=False, index=True),
        sa.Column("relation_type", sa.String(12), nullable=False, server_default="manage"),
        sa.Column("created_by", sa.String(36), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("hub_id", "yard_id", name="uq_hub_yards_hub_yard"),
    )

    # 回填：既有单归属视为 manage 关系（方言无关的 Python 侧 INSERT，可重复执行不报错）
    bind = op.get_bind()
    yards = sa.table(
        "yards",
        sa.column("id", sa.String),
        sa.column("hub_id", sa.String),
    )
    hub_yards = sa.table(
        "hub_yards",
        sa.column("id", sa.String),
        sa.column("hub_id", sa.String),
        sa.column("yard_id", sa.String),
        sa.column("relation_type", sa.String),
        sa.column("created_by", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    rows = bind.execute(
        sa.select(yards.c.id, yards.c.hub_id).where(
            yards.c.hub_id.is_not(None), yards.c.hub_id != ""
        )
    ).all()
    existing = {
        (row.hub_id, row.yard_id)
        for row in bind.execute(sa.select(hub_yards.c.hub_id, hub_yards.c.yard_id)).all()
    }
    now = datetime.now(UTC)
    for yard_id, hub_id in rows:
        if (hub_id, yard_id) in existing:
            continue
        bind.execute(
            hub_yards.insert().values(
                id=uuid4().hex,
                hub_id=hub_id,
                yard_id=yard_id,
                relation_type="manage",
                created_by="migration",
                created_at=now,
                updated_at=now,
            )
        )


def downgrade() -> None:
    op.drop_table("hub_yards")
