"""订阅计费 ORM（v3.71）：套餐目录、兑换码与订单。"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Index, String, Text
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


class BillingPlan(Base, TimestampMixin):
    """套餐目录（v3.80，审查 D-18）：**入库可运营**，不再写死在代码里。

    代码里的 ``plans.PLANS`` 只是首次部署的种子值：迁移会把它们写进本表，
    之后价格、卖点、额度项、排序与上下架都由运营在后台改，不需要发版。

    ``items`` 与 ``plans.PlanItem`` 同构：
    ``[{"metric":"tokens","window":"month","amount":2000000,"note":"..."}]``。
    metric 取值与配额模块一致（tokens/calls/cost），保证"卖什么"与"限什么"
    是同一套口径（审查 D-21）。
    """

    __tablename__ = "billing_plans"
    __table_args__ = (Index("ix_billing_plans_active_sort", "active", "sort_order"),)

    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # subscription|booster
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="subscription")
    price_yuan: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    tagline: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: 额度追加项（与配额指标同口径）
    items: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    #: 可对比的卖点条目（JSON 字符串数组）；运营可改，用于付费页对比表
    highlights: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 运营备注（不对外展示）
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sort_order: Mapped[int] = mapped_column(default=0, nullable=False)
    active: Mapped[bool] = mapped_column(nullable=False, default=True)


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
        # 渠道流水号唯一（与迁移 0085 同名同义）：同一 trade_no 只能核销一次。
        # create_all 新装库也必须有这条硬约束，否则并发回调查重只剩 TOCTOU。
        Index("uq_billing_orders_payment_trade_no", "payment_trade_no", unique=True),
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

    # --- 自助付费闭环（v3.79，审查 D-17）----------------------------------
    #: 支付渠道（manual/sandbox/wechat_pay/alipay）
    payment_provider: Mapped[str | None] = mapped_column(String(24), nullable=True)
    #: 渠道流水号：回调幂等的唯一凭据（同一 trade_no 重复回调只到账一次）。
    #: **唯一索引**与迁移 0085 同名（uq_billing_orders_payment_trade_no）：
    #: 迁移路径与 create_all 路径（新装 dev 库）都必须有硬约束，
    #: 否则并发回调的查重只剩 TOCTOU 先查后写。PG/SQLite 的唯一索引对
    #: 多行 NULL 均放行，不影响未支付订单。
    payment_trade_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 渠道侧支付完成时间
    payment_paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: 对公转账银行回单号（用户自助提交；真实性由平台核验）
    voucher_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 回单核验状态：submitted=已提交（金额预检一致）待平台核验 / manual=金额不一致转人工。
    #: 到账只走平台「确认到账」链路，没有"自动到账"态。
    voucher_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
