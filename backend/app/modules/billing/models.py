"""订阅计费 ORM（v3.71）：兑换码与订单。"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

CODE_UNUSED = "unused"
CODE_REDEEMED = "redeemed"
CODE_DISABLED = "disabled"
CODE_EXPIRED = "expired"
CODE_STATUSES: tuple[str, ...] = (CODE_UNUSED, CODE_REDEEMED, CODE_DISABLED, CODE_EXPIRED)

ORDER_PENDING = "pending"
ORDER_PAID = "paid"
ORDER_CANCELLED = "cancelled"
ORDER_STATUSES: tuple[str, ...] = (ORDER_PENDING, ORDER_PAID, ORDER_CANCELLED)
#: 到账即写配额的终态
PAID_STATUSES: tuple[str, ...] = (ORDER_PAID,)


class BillingCode(Base, TimestampMixin):
    """兑换码：平台管理员线下收款后生成，小院自助兑换即到账。"""

    __tablename__ = "billing_codes"
    __table_args__ = (Index("ix_billing_codes_status", "status"),)

    #: 对外展示的码值（KY-XXXX-XXXX-XXXX），唯一即安全凭据
    code: Mapped[str] = mapped_column(String(24), nullable=False, unique=True)
    plan_code: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default=CODE_UNUSED)
    # status 的索引在 __table_args__ 显式声明，避免与 index=True 双重生成
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    redeemed_yard_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    redeemed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class BillingOrder(Base, TimestampMixin):
    """开通订单：兑换码自动置为已支付；对公转账订单由平台管理员确认到账。"""

    __tablename__ = "billing_orders"
    __table_args__ = (
        Index("ix_billing_orders_yard_id", "yard_id"),
        Index("ix_billing_orders_status", "status"),
        # 兑换码与订单一对一：同一 code_id 只允许一张订单（审查 B2 数据库兜底）。
        # code_id 可空（对公转账订单无码），SQLite/Postgres 唯一索引视 NULL 互不
        # 相等，故多张无码订单不受限；并发兑换由服务层 CAS + 本索引双重裁决。
        Index("uq_billing_orders_code_id", "code_id", unique=True),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False)
    plan_code: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default=ORDER_PENDING)
    price_yuan: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    #: 对公转账凭证说明（回单号/银行流水），不存图片
    voucher_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    code_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    requester_id: Mapped[str] = mapped_column(String(36), nullable=False)
    requester_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 配额已写入的标记（防重复到账）
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
