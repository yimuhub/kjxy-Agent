"""首启状态表 setup_state（v3.74）。

桌面版此前在 sidecar 首启时 seed 演示账号，`/setup/status` 用「users 表非空」
判定，于是向导永远被跳过。这里新增单行状态表，并把存量库（已有用户）回填为
已初始化，保证升级用户不会被重新弹向导。

Revision ID: 0082_setup_state
Revises: 0081_media_assets
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0082_setup_state"
down_revision = "0081_media_assets"
branch_labels = None
depends_on = None

_TABLE = "setup_state"


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, _TABLE):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("initialized_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("admin_user_id", sa.String(length=36), nullable=True),
            sa.Column("yard_id", sa.String(length=36), nullable=True),
            sa.Column(
                "wizard_version",
                sa.String(length=20),
                nullable=False,
                server_default="3.74",
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    # 存量库：只要已有用户，就视为完成过初始化（含老桌面版的演示账号）。
    existing = bind.execute(sa.text("SELECT id FROM setup_state LIMIT 1")).fetchone()
    if existing is not None:
        return
    if not _has_table(bind, "users"):
        return
    user = bind.execute(
        sa.text("SELECT id FROM users WHERE platform_admin IS TRUE ORDER BY created_at ASC LIMIT 1")
    ).fetchone()
    if user is None:
        user = bind.execute(
            sa.text("SELECT id FROM users ORDER BY created_at ASC LIMIT 1")
        ).fetchone()
    if user is None:
        return
    yard = bind.execute(
        sa.text("SELECT id FROM yards WHERE owner_user_id = :uid ORDER BY created_at ASC LIMIT 1"),
        {"uid": user[0]},
    ).fetchone()
    bind.execute(
        sa.text(
            "INSERT INTO setup_state "
            "(id, initialized_at, admin_user_id, yard_id, wizard_version, created_at, updated_at) "
            "VALUES (:id, :now, :uid, :yid, 'legacy', :now, :now)"
        ),
        {
            "id": "00000000-0000-4000-8000-000000000001",
            "now": datetime.now(UTC),
            "uid": user[0],
            "yid": yard[0] if yard is not None else None,
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, _TABLE):
        op.drop_table(_TABLE)
