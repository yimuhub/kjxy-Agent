"""插件/技能市场条目（本地内置、远程索引、GitHub 检索与本地导入的统一缓存）。"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class MarketItem(Base, TimestampMixin):
    __tablename__ = "market_items"
    __table_args__ = (
        Index("ix_market_items_type_source", "item_type", "source"),
        Index("ix_market_items_status", "status"),
    )

    # 稳定键：source:type:code（安装与详情都按它寻址）
    item_key: Mapped[str] = mapped_column(String(220), unique=True, nullable=False, index=True)
    # skill | plugin | agent
    item_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # builtin | remote | github | local
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False, default="1.0.0")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    license: Mapped[str | None] = mapped_column(String(50), nullable=True)
    author: Mapped[str | None] = mapped_column(String(120), nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # low | medium | high（技能多为 low；插件按 workflow 步骤风险）
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    # available | installed | pending_review | blocked
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="available")
    definition_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    manifest_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
