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
    # 0033：归属与可见性（v3.2）。private 仅创建者与负责人可见；yard 全小院可用；
    # platform 为平台级（yard_id 为空）。
    visibility: Mapped[str] = mapped_column(
        String(16), nullable=False, default="yard", server_default="yard", index=True
    )
    owner_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    origin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # v3.17：定义摘要与签名。摘要在写入时计算；签名在发布/推广时产生，
    # 用于回答「这份已上架的技能，内容有没有在上架后被改过」。
    definition_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signature_alg: Mapped[str | None] = mapped_column(String(20), nullable=True)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    signed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: 签名密钥指纹：轮换密钥后可定位是哪把钥匙签的
    key_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


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
    # v3.17：上架前评测报告。kind=gate（数据集门禁）|static（静态质量检查）|install（市场安装）
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="gate", server_default="gate"
    )
    #: 完整报告（检查项、通过率、失败用例），可复现；不含参考正文
    report_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    report_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dataset_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
