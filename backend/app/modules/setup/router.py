"""系统初始化向导 REST API（/setup，v3.69）。

- GET  /setup/status     匿名：{initialized}（登录页/根路由据此分流）
- POST /setup/initialize 匿名：执行一次性初始化并返回自动登录 token

安全闸：仅当系统中**不存在任何用户**时可用；完成后永久 409。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.deps import get_db_session
from app.core.responses import ok
from app.modules.setup import service

router = APIRouter(prefix="/setup", tags=["setup"])


@router.get("/status")
def setup_status(request: Request, db: Session = Depends(get_db_session)):
    return ok({"initialized": service.is_initialized(db)}, request)


class InitializeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(min_length=1, max_length=64)
    email: str | None = Field(default=None, max_length=255)
    yard_name: str = Field(min_length=1, max_length=128)
    province: str = Field(default="", max_length=64)
    city: str = Field(default="", max_length=64)
    profile_types: list[str] = Field(default_factory=list, max_length=10)
    yard_description: str = Field(default="", max_length=64)
    app_name: str = Field(default="", max_length=40)
    primary_color: str = Field(default="", max_length=20)
    search_enabled: bool = True
    agent_generate_enabled: bool = True
    agent_generate_daily_limit: int = Field(default=10, ge=0, le=200)
    journal_enabled: bool = True
    deployment: str = Field(default="desktop", pattern="^(desktop|server|cloud)$")


@router.post("/initialize")
def initialize(
    payload: InitializeRequest,
    request: Request,
    db: Session = Depends(get_db_session),
):
    """一次性初始化：超管 + 首个小院 + 画像/品牌/开关，返回自动登录 token。"""
    result = service.initialize(
        db,
        request_id=request.headers.get("x-request-id", "setup"),
        username=payload.username,
        password=payload.password,
        display_name=payload.display_name,
        email=payload.email,
        yard_name=payload.yard_name,
        province=payload.province,
        city=payload.city,
        profile_types=payload.profile_types,
        yard_description=payload.yard_description,
        app_name=payload.app_name,
        primary_color=payload.primary_color,
        search_enabled=payload.search_enabled,
        agent_generate_enabled=payload.agent_generate_enabled,
        agent_generate_daily_limit=payload.agent_generate_daily_limit,
        journal_enabled=payload.journal_enabled,
        deployment=payload.deployment,
    )
    db.commit()
    return ok(result, request)
