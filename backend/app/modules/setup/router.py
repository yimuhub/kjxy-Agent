"""系统初始化向导 REST API（/setup，v3.74）。

- GET  /setup/status     匿名：{initialized}（启动页/根路由据此分流）
- GET  /setup/skills     匿名：可勾选的内置技能清单
- POST /setup/initialize 匿名：执行一次性初始化并返回自动登录 token

安全闸：仅当系统**从未初始化**时可用（setup_state 无记录且库中无用户）；
完成后永久 409。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_db_session
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.setup import service

router = APIRouter(prefix="/setup", tags=["setup"])


class LlmProviderIn(BaseModel):
    """向导第 4 步填写的云端供应商；api_key 加密落库，绝不回显。"""

    model_config = ConfigDict(extra="forbid")

    provider_type: str = Field(default="openai_compatible", max_length=40)
    name: str = Field(default="云端模型", max_length=40)
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str = Field(min_length=1, max_length=2000)
    model_id: str = Field(min_length=1, max_length=100)


class InitializeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(min_length=1, max_length=64)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=20)
    yard_name: str = Field(min_length=1, max_length=128)
    province: str = Field(default="", max_length=64)
    city: str = Field(default="", max_length=64)
    county: str = Field(default="", max_length=64)
    profile_types: list[str] = Field(default_factory=list, max_length=10)
    yard_description: str = Field(default="", max_length=64)
    school: str = Field(default="", max_length=40)
    industry: str = Field(default="", max_length=30)
    skill_codes: list[str] = Field(default_factory=list, max_length=200)
    llm_provider: LlmProviderIn | None = None
    app_name: str = Field(default="", max_length=40)
    primary_color: str = Field(default="", max_length=20)
    search_enabled: bool = True
    agent_generate_enabled: bool = True
    agent_generate_daily_limit: int = Field(default=10, ge=0, le=200)
    deployment: str = Field(default="desktop", pattern="^(desktop|server|cloud)$")


def _guard_remote_setup(request: Request) -> None:
    """生产部署默认只允许本机完成初始化，避免公网首个访客抢注管理员。"""
    host = (request.client.host if request.client else "") or ""
    if host in {"127.0.0.1", "::1", "localhost", "testclient"}:
        return
    if settings.setup_allow_remote or not settings.is_production:
        return
    raise AppError(
        "SETUP_LOCAL_ONLY",
        "初始化向导仅允许在本机访问；如需远程初始化，请设置 SETUP_ALLOW_REMOTE=true。",
        http_status=403,
    )


@router.get("/status")
def setup_status(request: Request, db: Session = Depends(get_db_session)):
    return ok({"initialized": service.is_initialized(db)}, request)


@router.get("/skills")
def setup_skills(request: Request):
    """向导第 4 步的技能清单（纯数据，不含任何密钥）。"""
    items = service.builtin_skill_catalog()
    return ok({"items": items, "total": len(items)}, request)


@router.post("/initialize")
def initialize(
    payload: InitializeRequest,
    request: Request,
    db: Session = Depends(get_db_session),
):
    """一次性初始化：超管 + 首个小院 + 技能 + 模型/品牌/开关，返回自动登录 token。"""
    _guard_remote_setup(request)
    result = service.initialize(
        db,
        request_id=request.headers.get("x-request-id", "setup"),
        username=payload.username,
        password=payload.password,
        display_name=payload.display_name,
        email=payload.email,
        phone=payload.phone,
        yard_name=payload.yard_name,
        province=payload.province,
        city=payload.city,
        county=payload.county,
        profile_types=payload.profile_types,
        yard_description=payload.yard_description,
        school=payload.school,
        industry=payload.industry,
        skill_codes=payload.skill_codes,
        llm_provider=payload.llm_provider.model_dump() if payload.llm_provider else None,
        app_name=payload.app_name,
        primary_color=payload.primary_color,
        search_enabled=payload.search_enabled,
        agent_generate_enabled=payload.agent_generate_enabled,
        agent_generate_daily_limit=payload.agent_generate_daily_limit,
        deployment=payload.deployment,
    )
    db.commit()
    return ok(result, request)
