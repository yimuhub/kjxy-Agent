"""插件注册表模型。"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class PluginManifest(Base, TimestampMixin):
    """插件注册表：扫描 plugins/ 目录后写入/更新。

    tenant_id 为空 = 全局（platform）插件，首版所有插件都走全局。
    status: enabled | disabled —— 停用只改状态，不删除业务数据。
    """

    __tablename__ = "plugin_manifests"
    __table_args__ = (
        UniqueConstraint("plugin_id", "tenant_id", name="uq_plugin_manifests_pid_tenant"),
    )

    plugin_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    tenant_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )  # 空=全局插件
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="disabled", index=True
    )  # enabled|disabled
    manifest_json: Mapped[dict] = mapped_column("manifest", JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    checksum_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    installed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
