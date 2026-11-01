"""模型绑定 ORM：``llm_bindings``（v3.25）。

一行 = 「某层用途优先用某供应商」。同层可有多行，按 ``priority`` 从大到小构成
兜底顺序；解析时命中不可用（供应商停用/密钥失效/连续失败被降级）自动落到下一行、
下一层，绝不因单个供应商故障中断业务。

- ``yard_id`` 为 NULL 表示平台级绑定，对全部小院生效；非空仅归属小院可见。
- ``scope_type``：``global``（小院/平台默认，``scope_key`` 恒为空串）、
  ``purpose``（能力级，``scope_key`` = 能力码）、
  ``agent``（单助手级，``scope_key`` = 助手编码 agent_code）。
- 解析优先级：Agent 绑定 → 能力绑定 → 默认绑定 → 小院 active 供应商 → 平台优先级 → settings 兜底。
"""

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

SCOPE_GLOBAL = "global"
SCOPE_PURPOSE = "purpose"
SCOPE_AGENT = "agent"
#: v3.37：助手 + 能力（``scope_key = "<agent_code>::<purpose>"``），
#: 让单个助手单独指定图像/视频/向量等能力模型，且不污染它的对话模型。
SCOPE_AGENT_PURPOSE = "agent_purpose"
SCOPE_TYPES: tuple[str, ...] = (SCOPE_GLOBAL, SCOPE_PURPOSE, SCOPE_AGENT, SCOPE_AGENT_PURPOSE)
#: agent_purpose 的 scope_key 分隔符
AGENT_PURPOSE_SEP = "::"

#: 同一层可配的兜底条数上限（3 条足够覆盖「主 + 备 + 备」，再多会掩盖配置错误）
MAX_FALLBACK_PER_SCOPE = 3


class LLMBinding(Base, TimestampMixin):
    __tablename__ = "llm_bindings"
    __table_args__ = (
        UniqueConstraint(
            "yard_id", "scope_type", "scope_key", "supplier_id", name="uq_llm_bindings_scope"
        ),
    )

    yard_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    supplier_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False
    )
    # v3.33：优先绑定到模型行（同一供应商下多模型）；为空时回落到 model_name
    supplier_model_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("supplier_models.id", ondelete="SET NULL"), nullable=True
    )
    # 覆盖上游物理模型名；空 = 沿用供应商自身 model_id
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
