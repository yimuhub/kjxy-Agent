"""0072 yards.attributes：小院特性字段（培养单位/县域/产业/类型/标签，v3.66）。

三种枢纽模式需要按不同维度分组（省级按培养单位、市级按县域、实验站按小院类型），
而 yards 之前只有 province/city/scene_type 三个自由文本列，不足以支撑分组与横向对比。
用单列 JSON 承载，避免为每种模式加一排稀疏列。
"""

import sqlalchemy as sa
from alembic import op

revision = "0072_yard_attributes"
down_revision = "0071_yard_hubs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("yards", sa.Column("attributes", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("yards", "attributes")
