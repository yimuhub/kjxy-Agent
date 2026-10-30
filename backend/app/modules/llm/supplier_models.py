"""模型供应商 ORM：suppliers（照抄 new-api Channel 精简）+ yard_active_suppliers。

- suppliers.yard_id 为 NULL 表示全局共享供应商，所有小院可见；
  非空则仅归属小院可见。
- status：1=启用，2=禁用（连续探测失败 3 次自动置 2）。
- yard_active_suppliers：小院级"当前生效供应商"（一小院一行）。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Supplier(Base, TimestampMixin):
    __tablename__ = "suppliers"

    # NULL=全局共享；非空=仅归属小院可见
    yard_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(40), nullable=False)  # 逻辑名，如 deepseek-main
    provider_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # deepseek|qwen|moonshot|openai|openai_compatible
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)  # 上游物理模型名
    api_key_enc: Mapped[str] = mapped_column(Text, nullable=False)  # Fernet 加密，永存密文
    models: Mapped[str | None] = mapped_column(Text, nullable=True)  # 白名单，逗号分隔，空=全量
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 越大越优先
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # 1=启用/2=禁用
    # function-calling 能力位：仅显式声明 true 的供应商才允许进入工具循环
    # （迁移 0013；默认 false → 旧行为零回归）。后端 PATCH 可设，前端表单 P2 补。
    supports_tools: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # v3.32 金额额度：单价（元 / 千 token）；空 = 未配置，按 Provider 默认常量估算
    price_in_per_1k: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_out_per_1k: Mapped[float | None] = mapped_column(Float, nullable=True)
    test_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class YardActiveSupplier(Base, TimestampMixin):
    __tablename__ = "yard_active_suppliers"

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, index=True)
    supplier_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("suppliers.id"), nullable=True
    )
