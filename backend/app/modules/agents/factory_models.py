"""Agent 工厂运行记录的 ORM 模型（迁移 0010_factory_runs）。

一条 factory_runs 行 = 一次「行业描述 → 蓝图 → 装配」的工厂运行。
status 生命周期：claimed → planning → generating → completed | failed。
blueprint_json 由流水线回写（每个角色含 agent_id / knowledge_coverage /
needs_knowledge / smoke_questions），apply 发布动作记 approved_by。
"""

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

# 运行状态（与迁移默认值/服务层保持一致）
STATUS_CLAIMED = "claimed"
STATUS_PLANNING = "planning"
STATUS_GENERATING = "generating"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
VALID_STATUSES = (
    STATUS_CLAIMED,
    STATUS_PLANNING,
    STATUS_GENERATING,
    STATUS_COMPLETED,
    STATUS_FAILED,
)

#: 蓝图来源（诚实标注）：LLM 产出解析成功=llm；兜底启发式=heuristic
GENERATED_BY_LLM = "llm"
GENERATED_BY_HEURISTIC = "heuristic"
VALID_GENERATED_BY = (GENERATED_BY_LLM, GENERATED_BY_HEURISTIC)


class FactoryRun(Base, TimestampMixin):
    __tablename__ = "factory_runs"

    yard_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    # 行业描述（必填输入），如「农机服务，侧重果园小型农机」
    industry_brief: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default=STATUS_CLAIMED)
    # 完整蓝图 JSON（含 coverage / needs_knowledge / agent_id 回写）
    blueprint_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # llm|heuristic；running 期间为 None，completed 必填
    generated_by: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # status=failed 时记录失败原因（不伪报）
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # 人工采纳并触发分级发布的操作人
    approved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
