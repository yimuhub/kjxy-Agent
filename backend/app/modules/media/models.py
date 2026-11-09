"""媒体资产（0081）：对话内生成的图片/视频产物与上游任务状态。

一张表承载三件事：

1. **产物**：图片记录已落盘的海报键与签名 URL；视频记录转存后的存储键；
2. **任务**：视频异步生成的上游 task_id、状态、错误分类与重试依据；
3. **计量**：按 created_by + created_at 统计每人每日张数/秒数，额度不依赖外部缓存。

文件本身不在库里：图片在 ``local_storage_path/posters``（沿用 v3.26 海报目录），
视频在 ``local_storage_path/media``。取流一律走签名 capability URL，不暴露目录。
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

KIND_IMAGE = "image"
KIND_VIDEO = "video"
VALID_KINDS = (KIND_IMAGE, KIND_VIDEO)

#: 等待用户确认（视频会真实计费，未确认不提交上游）
STATUS_AWAITING = "awaiting_confirmation"
#: 已提交上游，等待出片
STATUS_RUNNING = "running"
#: 产物已落盘可下载
STATUS_READY = "ready"
#: 失败（error_code 区分超时/鉴权/限流/内容审核/转存失败）
STATUS_FAILED = "failed"
VALID_STATUSES = (STATUS_AWAITING, STATUS_RUNNING, STATUS_READY, STATUS_FAILED)


class MediaAsset(Base, TimestampMixin):
    __tablename__ = "media_assets"
    __table_args__ = (
        Index("ix_media_assets_yard_created", "yard_id", "created_at"),
        Index("ix_media_assets_session_created", "session_id", "created_at"),
        Index("ix_media_assets_status_updated", "status", "updated_at"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    created_by: Mapped[str] = mapped_column(String(36), index=True, nullable=False)

    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=STATUS_RUNNING)

    #: 视频转存键（相对 media 根目录）；图片留空，走海报目录的 key
    storage_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: 海报目录键（图片专用）
    poster_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: 成品签名 URL（短期可重算，落库是为了历史消息刷新后仍可直接渲染）
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    mime: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: 视频上游任务 id（幂等键：同一任务只回填一次）
    upstream_task_id: Mapped[str | None] = mapped_column(String(200), index=True, nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: 到期清理时间（视频默认 30 天；图片为空=随海报目录长期保留）
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: 生成规格、执行者、降级说明、上游提交路径等可追溯信息
    meta_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
