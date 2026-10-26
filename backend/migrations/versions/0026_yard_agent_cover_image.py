"""助手卡片首图：``yard_agents.cover_image`` + ``cover_mime``。

Revision ID: 0026_yard_agent_cover_image
Revises: 0025_chat_participants
Create Date: 2026-10-26

背景（v2.0 W2 助手卡片视图）：小院助手页改成卡片/列表双视图后，卡片要有首图。

- 两列都可空——没上传过就是 NULL，前端回落到既有的 ``emoji`` 大字渲染，
  不留空板块；存量 30 行助手因此无需清洗。
- ``cover_image`` 存随机存储键（文件本体在本地存储后端，不落原始文件名），
  与对话附件同一条管道；``cover_mime`` 存规范 MIME，取图时无需再从键名猜类型。
- 不给存储键建索引、也不把键放进出参：对外只有 ``GET /agents/declarative/{id}/cover``
  这条同院可见的取图路径（见 ``agents/covers.py`` 的可见性口径）。

可逆：downgrade 删两列；已上传的图片文件成为孤儿（与附件删除同口径，
不引入清理任务——存储目录里几十 KB 的孤儿图不影响任何正确性）。
"""

import sqlalchemy as sa
from alembic import op

revision = "0026_yard_agent_cover_image"
down_revision = "0025_chat_participants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("yard_agents", sa.Column("cover_image", sa.String(255), nullable=True))
    op.add_column("yard_agents", sa.Column("cover_mime", sa.String(120), nullable=True))


def downgrade() -> None:
    op.drop_column("yard_agents", "cover_mime")
    op.drop_column("yard_agents", "cover_image")
