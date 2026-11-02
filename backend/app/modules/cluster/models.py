"""多集群节点：平台管理员登记其他部署实例，做版本/迁移一致性体检。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

STATUS_NEVER = "never"
STATUS_ONLINE = "online"
STATUS_DEGRADED = "degraded"
STATUS_OFFLINE = "offline"


class ClusterNode(Base, TimestampMixin):
    """一个可被平台总览纳管的本平台实例；不保存任何鉴权令牌。"""

    __tablename__ = "cluster_nodes"
    __table_args__ = (
        UniqueConstraint("name", name="uq_cluster_nodes_name"),
        UniqueConstraint("base_url", name="uq_cluster_nodes_base_url"),
        Index("ix_cluster_nodes_status", "status"),
        Index("ix_cluster_nodes_checked", "last_checked_at"),
    )

    name: Mapped[str] = mapped_column(String(60), nullable=False)
    #: 实例根地址（http/https，不含查询串与用户信息），探测时拼 /api/v1/meta
    base_url: Mapped[str] = mapped_column(String(300), nullable=False)
    note: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=STATUS_NEVER, server_default=STATUS_NEVER
    )
    version: Mapped[str] = mapped_column(String(40), nullable=False, default="", server_default="")
    migration_head: Mapped[str] = mapped_column(
        String(80), nullable=False, default="", server_default=""
    )
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: 只存人类可读的短错误（类型 + 消息），不存响应头/正文/密钥
    last_error: Mapped[str] = mapped_column(
        String(300), nullable=False, default="", server_default=""
    )
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
