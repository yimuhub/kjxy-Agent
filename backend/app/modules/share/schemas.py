"""跨院只读分享的请求/响应结构（v3.12）。"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ShareCreate(BaseModel):
    """创建分享：报表按区间重新生成后快照；诊断复制为脱敏摘要。"""

    resource_type: Literal["farm_report", "diagnosis"]
    expires_days: int = Field(default=7, ge=1, le=30)
    # 报表快照参数
    report_kind: Literal["log", "weekly", "quarterly"] | None = None
    start: date | None = None
    end: date | None = None
    plot_id: str | None = Field(default=None, max_length=36)
    # 诊断快照参数
    diagnosis_id: str | None = Field(default=None, max_length=36)

    @model_validator(mode="after")
    def _required_fields(self) -> "ShareCreate":
        if self.resource_type == "farm_report":
            if not (self.report_kind and self.start and self.end):
                raise ValueError("分享报表需要 report_kind、start、end")
            if self.start > self.end:
                raise ValueError("开始日期不能晚于结束日期")
            if (self.end - self.start).days > 366:
                raise ValueError("单次分享的报表区间不能超过一年")
        elif not self.diagnosis_id:
            raise ValueError("分享诊断需要 diagnosis_id")
        return self


class ShareOut(BaseModel):
    share_id: str
    resource_type: str
    resource_id: str
    title: str
    expires_at: datetime
    revoked_at: datetime | None
    view_count: int
    last_viewed_at: datetime | None
    created_at: datetime


class ShareCreated(ShareOut):
    """创建响应：原始 token 只在此处出现一次。"""

    token: str
    path: str


class PublicShare(BaseModel):
    title: str
    resource_type: str
    content_md: str
    created_at: datetime
    expires_at: datetime
    view_count: int
