"""分级额度 ORM（v3.32）：策略、用量计数、提额申请、临时额度。

设计口径：
- 额度按「作用域 × 指标 × 窗口」建模，作用域支持小院、Agent、Agent 分组、
  模型名与供应商，因此「诊断类 Agent 每天 20 万 token」「贵模型每天 5 元」
  都能落到同一张表；
- 用量写进 ``quota_counters``（唯一键 + 原子 upsert），不从 traces 现算，
  避免每次调用都扫全表，也让并发下的累加有明确语义；
- 超限后不是只能找管理员改数：``approve`` 动作会建 ``quota_requests``，
  批准即写 ``quota_grants``（带有效期），到期自动失效，不动原策略。
"""

from datetime import datetime

from app.db.base import Base, TimestampMixin
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

SCOPE_YARD = "yard"
SCOPE_AGENT = "agent"
SCOPE_AGENT_GROUP = "agent_group"
SCOPE_MODEL = "model"
SCOPE_SUPPLIER = "supplier"
SCOPE_TYPES: tuple[str, ...] = (
    SCOPE_YARD,
    SCOPE_AGENT,
    SCOPE_AGENT_GROUP,
    SCOPE_MODEL,
    SCOPE_SUPPLIER,
)
SCOPE_LABELS: dict[str, str] = {
    SCOPE_YARD: "全小院",
    SCOPE_AGENT: "单个 Agent",
    SCOPE_AGENT_GROUP: "Agent 分组",
    SCOPE_MODEL: "模型",
    SCOPE_SUPPLIER: "供应商",
}

METRIC_TOKENS = "tokens"
METRIC_CALLS = "calls"
METRIC_COST = "cost"
METRICS: tuple[str, ...] = (METRIC_TOKENS, METRIC_CALLS, METRIC_COST)
METRIC_LABELS: dict[str, str] = {
    METRIC_TOKENS: "Token 用量",
    METRIC_CALLS: "调用次数",
    METRIC_COST: "金额（元）",
}

WINDOW_DAY = "day"
WINDOW_MONTH = "month"
WINDOWS: tuple[str, ...] = (WINDOW_DAY, WINDOW_MONTH)
WINDOW_LABELS: dict[str, str] = {WINDOW_DAY: "每天", WINDOW_MONTH: "每月"}

ON_EXCEED_WARN = "warn"
ON_EXCEED_BLOCK = "block"
ON_EXCEED_APPROVE = "approve"
ON_EXCEED_MODES: tuple[str, ...] = (ON_EXCEED_WARN, ON_EXCEED_BLOCK, ON_EXCEED_APPROVE)
ON_EXCEED_LABELS: dict[str, str] = {
    ON_EXCEED_WARN: "只提醒",
    ON_EXCEED_BLOCK: "直接拦截",
    ON_EXCEED_APPROVE: "拦截并允许申请",
}

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"
REQUEST_STATUSES: tuple[str, ...] = (
    STATUS_PENDING,
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_CANCELLED,
    STATUS_EXPIRED,
)

SOURCE_MANUAL = "manual"
SOURCE_TEMPLATE = "template"


class QuotaPolicy(Base, TimestampMixin):
    __tablename__ = "quota_policies"
    __table_args__ = (
        UniqueConstraint(
            "yard_id", "scope_type", "scope_key", "metric", "window", name="uq_quota_policies_scope"
        ),
        Index("ix_quota_policies_yard_id", "yard_id"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    #: yard 作用域为空串；agent/model/supplier 用编码或名称；agent_group 用分组名
    scope_key: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    metric: Mapped[str] = mapped_column(String(16), nullable=False, default=METRIC_TOKENS)
    window: Mapped[str] = mapped_column(String(8), nullable=False, default=WINDOW_MONTH)
    #: tokens 用整数 token；calls 用次数；cost 用元（两位小数）
    limit_value: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    on_exceed: Mapped[str] = mapped_column(String(16), nullable=False, default=ON_EXCEED_APPROVE)
    #: manual=人工配置；template=环境模板生成（换模板时可被覆盖）
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=SOURCE_MANUAL)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class QuotaCounter(Base, TimestampMixin):
    __tablename__ = "quota_counters"
    __table_args__ = (
        UniqueConstraint(
            "yard_id",
            "scope_type",
            "scope_key",
            "metric",
            "window",
            "window_start",
            name="uq_quota_counters_scope",
        ),
        Index("ix_quota_counters_yard_id", "yard_id"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    metric: Mapped[str] = mapped_column(String(16), nullable=False)
    window: Mapped[str] = mapped_column(String(8), nullable=False)
    #: day → YYYY-MM-DD；month → YYYY-MM
    window_start: Mapped[str] = mapped_column(String(10), nullable=False)
    used: Mapped[float] = mapped_column(Float, nullable=False, default=0)


class QuotaRequest(Base, TimestampMixin):
    __tablename__ = "quota_requests"
    __table_args__ = (
        Index("ix_quota_requests_yard_id", "yard_id"),
        Index("ix_quota_requests_status", "status"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: 冗余作用域信息，策略被删后申请单仍可读
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    metric: Mapped[str] = mapped_column(String(16), nullable=False)
    window: Mapped[str] = mapped_column(String(8), nullable=False)
    current_limit: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    requested_limit: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(12), nullable=False, default=STATUS_PENDING)
    requester_id: Mapped[str] = mapped_column(String(36), nullable=False)
    requester_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 批准后临时额度的有效天数（0 = 跟随当前窗口）
    valid_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)


class QuotaGrant(Base, TimestampMixin):
    __tablename__ = "quota_grants"
    __table_args__ = (Index("ix_quota_grants_policy_id", "policy_id"),)

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: 追加额度（tokens/calls 为个数，cost 为元）
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
