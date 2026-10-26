"""身份与权限底座：负责人、设置域、角色、授权、注册审核、用户安全字段。

Revision ID: 0030_identity_rbac
Revises: 0029_chat_attachments_document
Create Date: 2026-10-27

迁移口径：
- 每个小院选一位负责人：优先用户名为 ``admin`` 的平台管理员（若全库仅 1 个小院），
  否则取最早加入的 yard_admin；负责人 membership 角色改为 ``yard_owner``；
- 历史 yard_admin 保持不变（除被选为负责人的那一位）；
- 新增表均为空表，不影响既有数据。
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0030_identity_rbac"
down_revision = "0029_chat_attachments_document"
branch_labels = None
depends_on = None


def _now() -> datetime:
    return datetime.now(UTC)


def upgrade() -> None:
    # ---- users：联系方式与安全字段 ----
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("phone", sa.String(32), nullable=True))
        batch.add_column(sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column(
                "must_change_password",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column("password_updated_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("token_version", sa.Integer(), nullable=False, server_default="0")
        )
    op.create_index("ix_users_phone", "users", ["phone"], unique=True)

    # ---- yards：负责人 ----
    op.add_column("yards", sa.Column("owner_user_id", sa.String(36), nullable=True))
    op.create_index("ix_yards_owner_user_id", "yards", ["owner_user_id"], unique=False)

    # ---- 新表 ----
    op.create_table(
        "yard_settings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("yard_id", sa.String(36), sa.ForeignKey("yards.id"), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("yard_id", "key", name="uq_yard_settings_yard_key"),
    )
    op.create_table(
        "yard_roles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("yard_id", sa.String(36), sa.ForeignKey("yards.id"), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("permissions_json", sa.JSON(), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("yard_id", "code", name="uq_yard_roles_yard_code"),
    )
    op.create_table(
        "yard_member_grants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("yard_id", sa.String(36), sa.ForeignKey("yards.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("grants_json", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("yard_id", "user_id", name="uq_yard_member_grants_yard_user"),
    )
    op.create_table(
        "yard_join_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("yard_id", sa.String(36), nullable=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="join"),
        sa.Column("yard_name", sa.String(128), nullable=True),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("reviewed_by", sa.String(36), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reject_reason", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_yard_join_requests_yard_status",
        "yard_join_requests",
        ["yard_id", "status"],
        unique=False,
    )

    # ---- 数据回填：负责人 ----
    conn = op.get_bind()
    yards = conn.execute(sa.text("SELECT id FROM yards ORDER BY created_at")).fetchall()
    admin = conn.execute(
        sa.text("SELECT id FROM users WHERE username = 'admin' LIMIT 1")
    ).fetchone()
    for (yard_id,) in yards:
        owner_id = None
        if admin is not None and len(yards) == 1:
            owner_id = admin[0]
        else:
            row = conn.execute(
                sa.text(
                    "SELECT user_id FROM yard_memberships "
                    "WHERE yard_id = :y AND role = 'yard_admin' AND status = 'active' "
                    "ORDER BY created_at LIMIT 1"
                ),
                {"y": yard_id},
            ).fetchone()
            owner_id = row[0] if row else None
        if not owner_id:
            continue
        conn.execute(
            sa.text("UPDATE yards SET owner_user_id = :o WHERE id = :y"),
            {"o": owner_id, "y": yard_id},
        )
        membership = conn.execute(
            sa.text(
                "SELECT id FROM yard_memberships WHERE yard_id = :y AND user_id = :u "
                "LIMIT 1"
            ),
            {"y": yard_id, "u": owner_id},
        ).fetchone()
        if membership is not None:
            conn.execute(
                sa.text(
                    "UPDATE yard_memberships SET role = 'yard_owner', status = 'active' "
                    "WHERE id = :m"
                ),
                {"m": membership[0]},
            )
        else:
            now = _now()
            conn.execute(
                sa.text(
                    "INSERT INTO yard_memberships "
                    "(id, yard_id, user_id, role, status, created_at, updated_at) "
                    "VALUES (:id, :y, :u, 'yard_owner', 'active', :c, :c)"
                ),
                {"id": f"owner-{yard_id[:12]}", "y": yard_id, "u": owner_id, "c": now},
            )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE yard_memberships SET role = 'yard_admin' WHERE role = 'yard_owner'"))
    op.drop_index("ix_yard_join_requests_yard_status", table_name="yard_join_requests")
    op.drop_table("yard_join_requests")
    op.drop_table("yard_member_grants")
    op.drop_table("yard_roles")
    op.drop_table("yard_settings")
    op.drop_index("ix_yards_owner_user_id", table_name="yards")
    op.drop_column("yards", "owner_user_id")
    op.drop_index("ix_users_phone", table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("token_version")
        batch.drop_column("password_updated_at")
        batch.drop_column("must_change_password")
        batch.drop_column("phone_verified_at")
        batch.drop_column("email_verified_at")
        batch.drop_column("phone")
