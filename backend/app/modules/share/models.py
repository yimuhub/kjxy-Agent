"""跨院只读分享（v3.12）：快照式分享链接。

设计口径：
- **快照而非实时**：创建时把内容渲染成 markdown 存进 ``payload_json``，
  外部访问只读这份快照，不再查业务表，避免跨院数据面扩大；
- **令牌只存哈希**：原始 token 只在创建响应里出现一次，库中存 SHA-256；
- **可撤销、可过期**：撤销立即失效；默认 7 天，最长 30 天；
- **匿名只读**：公开端点无需登录，只返回标题、快照内容与时间，不回传创建者。
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

SHARE_RESOURCE_TYPES = ("farm_report", "diagnosis")
SHARE_MAX_DAYS = 30


class ShareLink(Base, TimestampMixin):
    __tablename__ = "share_links"
    __table_args__ = (Index("ix_share_links_yard_created", "yard_id", "created_at"),)

    yard_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(20), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(36), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
