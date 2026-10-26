"""声明式 Agent 的 ORM 模型（迁移 0009_tenant_agents）。

一条 tenant_agents 行 = 一个"配置即 Agent"的声明式定义：
persona/任务模板/工具白名单/知识范围全部存 DB，不发版不加代码。
发布分级见 ADR-0005：risk_level 由纯函数 assess_risk 计算，
status 生命周期 draft → (pending_platform_review) → published → deprecated。
"""

from sqlalchemy import JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

# 状态/枚举常量（服务层与迁移默认值保持一致）
STATUS_DRAFT = "draft"
STATUS_PENDING = "pending_platform_review"
STATUS_PUBLISHED = "published"
STATUS_DEPRECATED = "deprecated"
VALID_STATUSES = (STATUS_DRAFT, STATUS_PENDING, STATUS_PUBLISHED, STATUS_DEPRECATED)

RISK_LOW = "low"
RISK_HIGH = "high"

CREATED_FROM_MANUAL = "manual"
CREATED_FROM_FACTORY = "factory"
CREATED_FROM_TEMPLATE = "template"
#: 对话内生成（chat/capability.py）：需求 → 草稿，来源标记与手工创建区分
CREATED_FROM_CHAT = "chat"
VALID_CREATED_FROM = (
    CREATED_FROM_MANUAL,
    CREATED_FROM_FACTORY,
    CREATED_FROM_TEMPLATE,
    CREATED_FROM_CHAT,
)


class TenantAgent(Base, TimestampMixin):
    __tablename__ = "tenant_agents"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "agent_code", "version", name="uq_tenant_agents_tenant_code_version"
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    agent_code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    emoji: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    persona_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    # 必须含 {{question}} 占位（服务端校验），运行时替换为用户问题
    task_prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_tools: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    knowledge_scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # 可选：指定本租户供应商；空则走现有租户激活供应商解析链
    supplier_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    risk_level: Mapped[str] = mapped_column(String(10), nullable=False, default=RISK_LOW)
    status: Mapped[str] = mapped_column(
        String(35), index=True, nullable=False, default=STATUS_DRAFT
    )
    version: Mapped[str] = mapped_column(String(50), nullable=False, default="1.0.0")
    created_from: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CREATED_FROM_MANUAL
    )
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
