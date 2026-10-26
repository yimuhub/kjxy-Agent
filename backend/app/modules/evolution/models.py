"""演进（自生长）模型：用户反馈、知识候选、Skill 版本。"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Feedback(Base, TimestampMixin):
    """用户对问答/任务结果的反馈。"""

    __tablename__ = "evolution_feedback"

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rating: Mapped[str | None] = mapped_column(String(10), nullable=True)  # up|down
    feedback_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")  # open|resolved


class KnowledgeCandidate(Base, TimestampMixin):
    """待审核的知识候选（主要来自用户纠错反馈）。"""

    __tablename__ = "evolution_knowledge_candidates"

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    source_feedback_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, default="user_feedback")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending_review", index=True
    )  # pending_review|approved|rejected
    reviewed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class Skill(Base, TimestampMixin):
    """可演进的 Skill 定义版本。"""

    __tablename__ = "evolution_skills"
    __table_args__ = (
        # yard_id 可空：PG 语义下 NULL 视为互不相等，P0 接受
        # （平台级 skill_code+version 仍可能重复，由 service 层做显式唯一校验兜底）。
        UniqueConstraint("yard_id", "skill_code", "version", name="uq_skill_yard_code_version"),
    )

    yard_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # 可空=平台级
    skill_code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    definition_json: Mapped[dict] = mapped_column("definition", JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", index=True
    )  # draft|testing|pending_review|published|deprecated
    created_from: Mapped[str | None] = mapped_column(String(100), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class SkillRollout(Base, TimestampMixin):
    """Skill 灰度发布配置：同一 Skill 双版本并存，按策略把部分小院切到新版。

    一个 (yard_id, skill_code) 同一时刻最多一条未完成（active|paused）的灰度。
    """

    __tablename__ = "evolution_skill_rollouts"

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    skill_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evolution_skills.id"), nullable=False
    )
    skill_code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    stable_version: Mapped[str] = mapped_column(String(50), nullable=False)
    rollout_version: Mapped[str] = mapped_column(String(50), nullable=False)
    # yard_allowlist | percentage
    strategy: Mapped[str] = mapped_column(String(20), nullable=False)
    percentage: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0-100
    yard_allowlist: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # active | paused | completed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)


class EvalRun(Base, TimestampMixin):
    """评测门禁运行记录（第四期 4.2）：每次发布门禁跑完评估集后插一条。

    gate 放行 / 拦截 / skip_gate（不跑评估集）分别在审计日志中留痕；
    本表只记录实际跑完评估集的结果（含被拦截的那一次）。
    """

    __tablename__ = "eval_runs"
    __table_args__ = (Index("ix_eval_runs_lookup", "yard_id", "skill_code", "skill_version"),)

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    skill_code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    skill_version: Mapped[str] = mapped_column(String(50), nullable=False)
    test_set_id: Mapped[str] = mapped_column(String(100), nullable=False)
    pass_rate: Mapped[float] = mapped_column(Float, nullable=False)
    passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
