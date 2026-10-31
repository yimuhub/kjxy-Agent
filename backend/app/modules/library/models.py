"""官方库 ORM：源、条目版本、小院安装台账与同步记录。

口径：
- ``library_sources.yard_id`` 为空 = 平台官方源（所有小院可读），非空 = 私有源；
- ``library_items`` 按 (source_id, item_key, version) 唯一，同一 item_key 保留多版本，
  以便离线回滚与「旧版本保留」；``item_key`` 形如 ``official:skill:<code>``；
- ``library_installs`` 按 (yard_id, item_key) 唯一：安装是「条目」级台账，
  版本变化写入 installed_version/previous_version；
- 撤销只改 status（revoked/blocked），绝不删除已安装数据。
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class LibrarySource(Base, TimestampMixin):
    __tablename__ = "library_sources"
    __table_args__ = (
        Index("ix_library_sources_yard_id", "yard_id"),
        Index("ix_library_sources_kind", "kind"),
    )

    #: 空 = 平台官方源；非空 = 该小院私有源
    yard_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    #: official | private（official 只允许平台管理员创建/编辑）
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="official")
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    #: stable | beta
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="stable")
    #: 发布者 Ed25519 公钥（base64url，32 字节）；绝不存私钥
    public_key_b64: Mapped[str] = mapped_column(Text, nullable=False, default="")
    key_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: 上次清单的 ETag（If-None-Match 条件请求）
    etag: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: 清单单调序号：小于等于当前值的清单直接跳过
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: unconfigured | ok | error
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="unconfigured")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 同步周期等运维配置（sync_interval_minutes），不含任何密钥
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class LibraryItem(Base, TimestampMixin):
    __tablename__ = "library_items"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "item_key", "version", name="uq_library_items_source_item_version"
        ),
        Index("ix_library_items_item_key", "item_key"),
        Index("ix_library_items_status", "status"),
    )

    source_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("library_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: official:<item_type>:<code>
    item_key: Mapped[str] = mapped_column(String(220), nullable=False)
    #: skill | plugin | agent_template | deck_theme | doc_template
    item_type: Mapped[str] = mapped_column(String(24), nullable=False)
    code: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    min_app_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    max_app_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    license: Mapped[str | None] = mapped_column(String(50), nullable=True)
    author: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: .kjxpack 整体 SHA-256（下载后必须重算并比对）
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    download_url: Mapped[str] = mapped_column(String(500), nullable=False)
    #: 清单内嵌的包元数据（可空，安装时以包内 manifest 为准）
    manifest_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    #: available | installed | blocked | revoked
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="available")
    #: 清单序号，用于「最新版」判定
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LibraryInstall(Base, TimestampMixin):
    __tablename__ = "library_installs"
    __table_args__ = (
        UniqueConstraint("yard_id", "item_key", name="uq_library_installs_yard_item"),
        Index("ix_library_installs_yard_id", "yard_id"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    item_key: Mapped[str] = mapped_column(String(220), nullable=False)
    item_type: Mapped[str] = mapped_column(String(24), nullable=False)
    installed_version: Mapped[str] = mapped_column(String(50), nullable=False)
    #: 上一版本（Skill 版本表仍然保留，供离线回滚）
    previous_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    package_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    skill_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    plugin_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    auto_update: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LibrarySyncRun(Base, TimestampMixin):
    __tablename__ = "library_sync_runs"
    __table_args__ = (Index("ix_library_sync_runs_source_id", "source_id"),)

    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: 官方源由平台触发时为空
    yard_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    #: ok | error | skipped
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    added: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    removed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
