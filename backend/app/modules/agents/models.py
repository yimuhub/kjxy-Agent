"""声明式 Agent 的 ORM 模型（迁移 0009_yard_agents，扩展见 0023-0027）。

一条 yard_agents 行 = 一个"配置即 Agent"的声明式定义：
persona/任务模板/工具白名单/多技能绑定/知识范围/绑定模型全部存 DB，不发版不加代码。
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
#: 软删除终态：行保留（chat 历史/班组/checkpoint 引用不悬空），所有读路径一律隐藏
STATUS_DELETED = "deleted"
VALID_STATUSES = (
    STATUS_DRAFT,
    STATUS_PENDING,
    STATUS_PUBLISHED,
    STATUS_DEPRECATED,
    STATUS_DELETED,
)

# 可见性（小院「管理员执行/成员申请」口径）：
# public = 小院管理员创建，全院可见可用；private = 仅创建者可见可用。
VISIBILITY_PUBLIC = "public"
VISIBILITY_PRIVATE = "private"
VALID_VISIBILITIES = (VISIBILITY_PUBLIC, VISIBILITY_PRIVATE)

RISK_LOW = "low"
RISK_HIGH = "high"

CREATED_FROM_MANUAL = "manual"
CREATED_FROM_FACTORY = "factory"
CREATED_FROM_TEMPLATE = "template"
#: 对话内生成（chat/capability.py）：需求 → 草稿，来源标记与手工创建区分
CREATED_FROM_CHAT = "chat"
#: 一句话生成智能体（agent_builder.py，v3.5）：编码带 gen_ 前缀
CREATED_FROM_GENERATED = "generated"
#: 指挥官拆解任务时自动创建（v3.38，默认草稿态）；与「一句话生成」区分来源展示
CREATED_FROM_COMMANDER = "commander"
VALID_CREATED_FROM = (
    CREATED_FROM_MANUAL,
    CREATED_FROM_FACTORY,
    CREATED_FROM_TEMPLATE,
    CREATED_FROM_CHAT,
    CREATED_FROM_GENERATED,
    CREATED_FROM_COMMANDER,
)


class YardAgent(Base, TimestampMixin):
    __tablename__ = "yard_agents"
    __table_args__ = (
        UniqueConstraint(
            "yard_id", "agent_code", "version", name="uq_yard_agents_yard_code_version"
        ),
        # 助手展示名同小院唯一（与 devices/mcp/push/bots 同风格手写命名，无 naming_convention）
        UniqueConstraint("yard_id", "name", name="uq_yard_agents_yard_name"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    agent_code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    emoji: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    persona_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    # 必须含 {{question}} 占位（服务端校验），运行时替换为用户问题
    task_prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_tools: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # 0027 新增：1 对 N 绑定的多维技能列表（支持 evolution_skills / workflow plugins / 扩展技能包）
    bound_skills: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    knowledge_scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # 可选：指定本小院供应商；空则走现有小院激活供应商解析链
    supplier_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # 0027 新增：可选具体大模型名称覆写（同一供应商下不同 Agent 可绑定不同子模型）
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # v3.32：Agent 分组（如「诊断」「营销」），让一类助手共用同一条额度策略
    group_name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    risk_level: Mapped[str] = mapped_column(String(10), nullable=False, default=RISK_LOW)
    status: Mapped[str] = mapped_column(
        String(35), index=True, nullable=False, default=STATUS_DRAFT
    )
    # public=全院可见可用（管理员创建）；private=仅创建者（成员创建）。
    # 展示后缀不进 name 列：唯一约束与 @ 提及列表继续作用于干净 name。
    visibility: Mapped[str] = mapped_column(String(10), nullable=False, default=VISIBILITY_PUBLIC)
    version: Mapped[str] = mapped_column(String(50), nullable=False, default="1.0.0")
    created_from: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CREATED_FROM_MANUAL
    )
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # 卡片首图：只存随机存储键（文件本体在本地存储后端），无图时前端回落 emoji
    cover_image: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cover_mime: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # v3.5 一句话生成：框架六段 + 参考来源 + 生成元信息（仅生成草稿非空）
    blueprint_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # v3.5：模型认为需要、但平台当前没有匹配到的技能（[{need,keywords,reason,status}]）
    skill_requirements: Mapped[list | None] = mapped_column(JSON, nullable=True)
