"""认证模块数据模型。"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # active | pending（待注册审核） | disabled
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    phone_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 管理员重置/新建账号后强制首登改密
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    password_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 令牌版本：改密/停用/改角色后递增，旧令牌立即失效
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class RefreshSession(Base, TimestampMixin):
    """刷新令牌会话：支持轮换与登出吊销（v3.7）。

    每次 refresh 都换新 jti 并把旧行标记 revoked；同一令牌被再次使用视为泄露，
    服务端吊销该用户全部活跃会话。expires_at 与 JWT exp 一致，过期行可安全清理。
    """

    __tablename__ = "refresh_sessions"
    __table_args__ = (Index("ix_refresh_sessions_user_active", "user_id", "revoked_at"),)

    jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    yard_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
