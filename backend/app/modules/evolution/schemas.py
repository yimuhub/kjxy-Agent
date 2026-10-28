"""演进模块 Schema。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Rating = Literal["up", "down"]
CandidateStatus = Literal["pending_review", "approved", "rejected"]
CandidateAction = Literal["approve", "reject"]
SkillStatus = Literal["draft", "testing", "pending_review", "published", "deprecated"]
SkillReviewAction = Literal["submit", "approve", "reject", "rollback", "submit_public"]
#: 技能列表作用域：mine=我的（含私有）| yard=小院公共库 | platform=平台级
SkillScope = Literal["mine", "yard", "platform"]


class FeedbackCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str | None = None
    rating: Rating | None = None
    feedback_text: str | None = Field(default=None, max_length=5000)
    corrected_answer: str | None = Field(default=None, max_length=20000)


class FeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    yard_id: str
    task_id: str | None
    actor_id: str
    rating: str | None
    feedback_text: str | None
    corrected_answer: str | None
    status: str
    created_at: datetime


class CandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    yard_id: str
    source_feedback_id: str | None
    title: str
    content: str
    source_type: str
    status: str
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_note: str | None
    created_at: datetime
    # S1：approve 后知识库入库结果。非审核动作或历史行默认视为成功；
    # 入库失败时 ingest_ok=false 且 ingest_error 携带原因，禁止伪报纯成功。
    ingest_ok: bool = True
    ingest_error: str | None = None
    # 双层验收（借鉴 agency 思想）均为展示性预筛，不影响审核动作与结果本体：
    # machine_precheck：机器预检结果 {pass, issues:[{criterion, why}]}，每条 why 带原话引用；
    # deep_precheck：LLM 深检结果，仅单条审核且 LLM_PRECHECK_ENABLED=true 时返回。
    machine_precheck: dict[str, Any] | None = None
    deep_precheck: dict[str, Any] | None = None


class CandidateReview(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: CandidateAction
    note: str | None = Field(default=None, max_length=2000)


class CandidateBatchReview(BaseModel):
    """批量审核请求：逐条执行单条审核动作，单条失败不影响其余。"""

    model_config = ConfigDict(extra="ignore")

    ids: list[str] = Field(min_length=1, max_length=100, description="候选 ID 列表（1-100 条）")
    action: CandidateAction
    review_note: str | None = Field(default=None, max_length=2000)


class CandidateBatchFailure(BaseModel):
    """批量审核中单条失败明细。"""

    id: str
    error_code: str
    error_message: str


class CandidateBatchReviewOut(BaseModel):
    """批量审核结果：approved/rejected 为成功条目 ID；failed 为失败明细。"""

    approved: list[str]
    rejected: list[str]
    failed: list[CandidateBatchFailure]


class SkillCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    skill_code: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=50)
    definition: dict = Field(description="Skill 定义 JSON")
    created_from: str | None = Field(default=None, max_length=100)
    # v3.2：一句话生成默认落私有草稿；人工创建保持小院可见
    visibility: str = Field(default="yard", pattern="^(private|yard|platform)$")


class SkillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    yard_id: str | None
    skill_code: str
    version: str
    definition_json: dict = Field(alias="definition")
    status: str
    created_from: str | None
    approved_by: str | None
    created_at: datetime
    visibility: str = "yard"
    owner_user_id: str | None = None
    origin: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None
    # v3.17：定义摘要与签名（签名正文只在 /skills/{id}/attestation 返回）
    definition_sha256: str | None = None
    signature_alg: str | None = None
    key_id: str | None = None
    signed_by: str | None = None
    signed_at: datetime | None = None


class SkillReview(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: SkillReviewAction
    note: str | None = Field(default=None, max_length=2000)


class SkillSuggest(BaseModel):
    """AI 技能总结请求（v1.3-B C1）：可选关注点提示，纯文本 ≤200 字。"""

    model_config = ConfigDict(extra="ignore")

    focus_hint: str | None = Field(default=None, max_length=200)


class EvalRunOut(BaseModel):
    """评测门禁运行记录（第四期 4.2）。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    yard_id: str
    skill_code: str
    skill_version: str
    test_set_id: str
    pass_rate: float
    passed: int
    failed: int
    total: int
    created_by: str | None
    created_at: datetime
    # v3.17：报告类型与摘要（完整报告按需从 /skills/{id}/eval-runs/{run_id} 取）
    kind: str = "gate"
    report_sha256: str | None = None
    dataset_sha256: str | None = None


RolloutStrategy = Literal["yard_allowlist", "percentage"]
RolloutStatus = Literal["active", "paused", "completed"]


class RolloutCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rollout_version: str = Field(min_length=1, max_length=50, description="灰度中的新版本号")
    strategy: RolloutStrategy
    percentage: int | None = Field(default=None, ge=0, le=100, description="百分比策略 0-100")
    yard_ids: list[str] | None = Field(default=None, description="名单策略的小院 ID 列表")


class RolloutOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    yard_id: str
    skill_code: str
    stable_version: str
    rollout_version: str
    strategy: str
    percentage: int | None
    yard_allowlist: list[str] | None = Field(alias="yard_ids")
    status: str
    created_at: datetime
    updated_at: datetime
