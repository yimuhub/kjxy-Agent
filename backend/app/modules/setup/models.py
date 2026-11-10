"""系统初始化状态（v3.74）。

一张单行表回答「这台机器是否已经完成首次安装」，取代此前「users 表是否非空」
的判定——后者会被桌面版首启 seed 的演示账号抢先置真，导致安装向导永远进不去。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class SetupState(Base, TimestampMixin):
    __tablename__ = "setup_state"

    initialized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    admin_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    yard_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    wizard_version: Mapped[str] = mapped_column(String(20), nullable=False, default="3.74")
