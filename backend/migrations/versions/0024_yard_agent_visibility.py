"""助手可见性：``yard_agents.visibility``（public/private）。

Revision ID: 0024_yard_agent_visibility
Revises: 0023_yard_agent_unique_name
Create Date: 2026-10-26

背景（v2.0 W2 助手私有/公用）：同小院内过去"已发布即全院可用"，成员自建的
助手别人也能看见并用——与"管理员建公共助手、成员建自己的助手"的产品口径不符。

- ``visibility``：`public` = 本小院全员可见可用（管理员创建一律 public）；
  `private` = 仅创建者可见可用。存量行全部落 public，行为与改名前一致。
- 只加列、不加索引：可见性只有两值，过滤条件始终与 `yard_id`/`status` 同行，
  现有 `ix_yard_agents_yard_id` 已够用，加索引只会增加写放大。
- 展示用的"（用户名+私有）"后缀在服务层派生，不写进 `name` 列——否则
  `uq_yard_agents_yard_name`（0023）会被后缀污染，@ 提及列表也会出现脏名字。
- `status` 新增 `deleted`（管理员软删除）与 `private` 一样是取值域扩展，
  列宽 String(35) 足够，无需改列类型。

可逆：downgrade 删列即可，不涉及数据改写。
"""

import sqlalchemy as sa
from alembic import op

revision = "0024_yard_agent_visibility"
down_revision = "0023_yard_agent_unique_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "yard_agents",
        sa.Column("visibility", sa.String(10), nullable=False, server_default="public"),
    )


def downgrade() -> None:
    op.drop_column("yard_agents", "visibility")
