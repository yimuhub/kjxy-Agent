"""演进模块 Schema。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Rating = Literal["up", "down"]
CandidateStatus = Literal["pending_review", "approved", "rejected"]
CandidateAction = Literal["approve", "reject"]
SkillStatus = Literal["draft", "testing", "pending_review", "published", "deprecated"]
SkillReviewAction = Literal["submit", "approve", "reject", "rollback"]


class FeedbackCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str | None = None
    rating: Rating | None = None
    feedback_text: str | None = Field(default=None, max_length=5000)
    corrected_answer: str | None = Field(default=None, max_length=20000)


class FeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
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
    tenant_id: str
    source_feedback_id: str | None
    title: str
    content: str
    source_type: str
    status: str
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_note: str | None
    created_at: datetime


class CandidateReview(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: CandidateAction
    note: str | None = Field(default=None, max_length=2000)


class SkillCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    skill_code: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=50)
    definition: dict = Field(description="Skill 定义 JSON")
    created_from: str | None = Field(default=None, max_length=100)


class SkillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    tenant_id: str | None
    skill_code: str
    version: str
    definition_json: dict = Field(alias="definition")
    status: str
    created_from: str | None
    approved_by: str | None
    created_at: datetime


class SkillReview(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: SkillReviewAction
    note: str | None = Field(default=None, max_length=2000)


class EvalRunOut(BaseModel):
    """评测门禁运行记录（第四期 4.2）。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    skill_code: str
    skill_version: str
    test_set_id: str
    pass_rate: float
    passed: int
    failed: int
    total: int
    created_by: str | None
    created_at: datetime


RolloutStrategy = Literal["tenant_allowlist", "percentage"]
RolloutStatus = Literal["active", "paused", "completed"]


class RolloutCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rollout_version: str = Field(min_length=1, max_length=50, description="灰度中的新版本号")
    strategy: RolloutStrategy
    percentage: int | None = Field(default=None, ge=0, le=100, description="百分比策略 0-100")
    tenant_ids: list[str] | None = Field(default=None, description="名单策略的租户 ID 列表")


class RolloutOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    tenant_id: str
    skill_code: str
    stable_version: str
    rollout_version: str
    strategy: str
    percentage: int | None
    tenant_allowlist: list[str] | None = Field(alias="tenant_ids")
    status: str
    created_at: datetime
    updated_at: datetime
