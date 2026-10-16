"""政策模块 Schema：PolicyOut 输出 / PolicyIngestRequest 采集输入 /
PolicyProfile 画像 / PolicyMatchResult 匹配结果。

第三期实现（采集、过期标记、规则匹配），不再是 P1 桩。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str | None
    scope: str
    province: str | None
    city: str | None
    department: str | None
    policy_name: str
    publish_department: str | None
    published_at: datetime | None
    apply_start_at: datetime | None
    apply_end_at: datetime | None
    policy_type: str | None
    support_direction: str | None
    apply_conditions_json: dict | None
    material_requirements_json: dict | None
    content: str | None
    source_url: str | None
    source_hash: str | None
    first_crawled_at: datetime | None
    last_checked_at: datetime | None
    verified_at: datetime | None
    status: str
    review_status: str
    created_at: datetime


class PolicyIngestRequest(BaseModel):
    """政策采集输入。

    apply_conditions_json 结构化申报条件示例：
        {"crops": ["水稻"], "min_area_mu": 50, "applicant_types": ["合作社"]}
    """

    policy_name: str = Field(..., min_length=1)
    publish_department: str = Field(..., min_length=1)
    published_at: datetime | None = None
    scope: Literal["national", "province", "city"] = "national"
    province: str | None = None
    city: str | None = None
    department: str | None = None
    policy_type: str | None = None
    support_direction: str | None = None
    apply_conditions_json: dict | None = None
    material_requirements_json: dict | None = None
    content: str | None = None
    source_url: str | None = None
    apply_start_at: datetime | None = None
    apply_end_at: datetime | None = None


class PolicyProfile(BaseModel):
    """待匹配画像：任一字段都可缺省，缺省导致对应条件无法判断。"""

    crops: list[str] | None = None
    area_mu: float | None = None
    province: str | None = None
    city: str | None = None
    applicant_type: str | None = None


class PolicyMatchResult(BaseModel):
    """单条政策的规则匹配结果。

    首版只做规则比对，不做成功率预测（规范 13.4）。
    """

    policy_id: str
    policy_name: str
    match_level: Literal["eligible", "partial", "insufficient", "ineligible"]
    matched_conditions: list[str]
    unmet_conditions: list[str]
    unknown_conditions: list[str]
    source_url: str | None
    verification_status: str


# ---------- 申报材料草案（第三期 Worker B） ----------


class ApplicationDraftCreate(BaseModel):
    """生成申报材料草案的请求体。"""

    applicant_name: str = Field(min_length=1)
    applicant_type: str | None = None
    contact_info: dict | None = None
    form_data: dict | None = None

    @field_validator("applicant_name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("申请人姓名必填")
        return v


class ChecklistItem(BaseModel):
    """材料清单条目。"""

    name: str
    required: bool
    source: str = "政策要求"


class ApplicationDraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    policy_id: str
    applicant_name: str
    applicant_type: str | None
    contact_info: dict | None
    form_data: dict | None
    material_checklist: list | None
    missing_materials: list | None
    notes: str | None
    status: str
    created_at: datetime
