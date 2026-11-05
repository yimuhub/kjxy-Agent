"""0075 users.platform_admin：向导创建的超级管理员持久化标志（v3.69）。"""

import sqlalchemy as sa
from alembic import op

revision = "0075_users_platform_admin"
down_revision = "0074_platform_branding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("platform_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "platform_admin")
