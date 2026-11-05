"""系统初始化服务（v3.69）：全新部署的一次性引导。

安全闸：``is_initialized`` = 已存在任何用户。向导只在**零用户**的全新部署
可用；初始化完成后端点永久 409（CONFIG_ALREADY_DONE），不留任何旁路。
"""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.security import create_access_token
from app.modules.auth.models import User
from app.modules.auth.repository import create_user
from app.modules.branding.service import update_settings as update_branding
from app.modules.journal.models import YardJournal  # noqa: F401 - 注册模型
from app.modules.yards.models import Yard
from app.modules.yards.repository import add_membership
from app.modules.yards.settings_service import update_setting

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")


def is_initialized(db: Session) -> bool:
    """已存在任何用户即视为已初始化（向导不可再次进入）。"""
    return int(db.scalar(select(func.count()).select_from(User)) or 0) > 0


def _validate_password(password: str) -> None:
    if len(password) < 10:
        raise AppError(
            "SETUP_WEAK_PASSWORD",
            "口令至少 10 位。",
            http_status=422,
            details={"field": "password"},
        )
    if not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        raise AppError(
            "SETUP_WEAK_PASSWORD",
            "口令必须同时包含字母与数字。",
            http_status=422,
            details={"field": "password"},
        )


def _settings_ctx(yard_id: str, user_id: str) -> YardContext:
    return YardContext(
        yard_id=yard_id, user_id=user_id, roles=["platform_admin"], request_id="setup"
    )


def initialize(
    db: Session,
    *,
    request_id: str,
    # 第 2 步：超级管理员
    username: str,
    password: str,
    display_name: str,
    email: str | None = None,
    # 第 3 步：小院
    yard_name: str,
    province: str = "",
    city: str = "",
    profile_types: list[str] | None = None,
    yard_description: str = "",
    # 第 4 步：品牌
    app_name: str = "",
    primary_color: str = "",
    # 第 5 步：功能开关
    search_enabled: bool = True,
    agent_generate_enabled: bool = True,
    agent_generate_daily_limit: int = 10,
    journal_enabled: bool = True,
    deployment: str = "desktop",
) -> dict[str, Any]:
    """执行一次性初始化：超管 + 首个小院 + 画像/品牌/功能开关，单事务。"""
    if is_initialized(db):
        raise AppError(
            "SETUP_ALREADY_DONE",
            "系统已完成初始化，向导不可再次进入。",
            http_status=409,
        )
    username = username.strip()
    if not USERNAME_RE.match(username):
        raise AppError(
            "SETUP_INVALID_USERNAME",
            "登录账号需为 3-32 位字母、数字或下划线。",
            http_status=422,
            details={"field": "username"},
        )
    if get_user_by_name(db, username) is not None:
        raise AppError(
            "SETUP_USERNAME_TAKEN",
            "该登录账号已存在。",
            http_status=409,
            details={"field": "username"},
        )
    _validate_password(password)
    yard_name = yard_name.strip()
    if not yard_name:
        raise AppError(
            "SETUP_YARD_NAME_REQUIRED",
            "请填写小院名称。",
            http_status=422,
            details={"field": "yard_name"},
        )

    # ① 超级管理员（持久标志：登录即授予 platform_admin）
    user = create_user(
        db,
        username=username,
        password=password,
        email=(email or None),
        display_name=display_name.strip() or username,
        status="active",
    )
    user.platform_admin = True
    db.flush()

    # ② 首个小院 + 负责人成员关系
    yard = Yard(
        name=yard_name[:128],
        status="active",
        province=(province or None),
        city=(city or None),
        owner_user_id=user.id,
    )
    db.add(yard)
    db.flush()
    add_membership(db, yard_id=yard.id, user_id=user.id, role="yard_owner")

    # ③ 小院画像（官方技能包推荐）+ 简介（写 scene_type 备用字段）
    types = [t for t in (profile_types or []) if t] or ["general"]
    if "general" not in types:
        types.append("general")
    update_setting(
        db,
        yard_id=yard.id,
        key="yard_profile",
        value={"types": types, "deployment": deployment},
        actor_id=user.id,
        request_id=request_id,
    )
    if yard_description:
        yard.scene_type = yard_description[:64]

    # ④ 品牌与外观（复用平台品牌包；名称/主色，Logo 进入系统后上传）
    branding_payload: dict[str, Any] = {}
    if app_name.strip():
        branding_payload["app_name"] = app_name.strip()[:40]
    if primary_color.strip():
        branding_payload["primary_color"] = primary_color.strip()
    if branding_payload:
        update_branding(db, actor_id=user.id, request_id=request_id, payload=branding_payload)

    # ⑤ 功能开关（只写用户显式选择的，其余用默认）
    _settings_ctx(yard.id, user.id)
    update_setting(
        db,
        yard_id=yard.id,
        key="search",
        value={"enabled": bool(search_enabled)},
        actor_id=user.id,
        request_id=request_id,
    )
    update_setting(
        db,
        yard_id=yard.id,
        key="agent_generate",
        value={
            "enabled": bool(agent_generate_enabled),
            "daily_limit": int(agent_generate_daily_limit),
            "use_web": True,
        },
        actor_id=user.id,
        request_id=request_id,
    )
    if not journal_enabled:
        # 工作日志按角色默认开放（logs:write），关闭仅影响向导推荐口径
        pass

    log_audit_setup(db, yard_id=yard.id, actor_id=user.id, username=username)

    token = create_access_token(
        user_id=user.id,
        yard_id=yard.id,
        roles=["platform_admin", "yard_owner"],
        token_version=0,
    )
    return {
        "user_id": user.id,
        "yard_id": yard.id,
        "username": user.username,
        "access_token": token,
    }


def get_user_by_name(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def log_audit_setup(db: Session, *, yard_id: str, actor_id: str, username: str) -> None:
    from app.modules.audit.service import log_audit

    log_audit(
        db,
        yard_id=yard_id,
        actor_id=actor_id,
        action="system_initialized",
        resource_type="system",
        resource_id=yard_id,
        result="success",
        request_id="setup",
        metadata={"username": username, "via": "setup_wizard"},
    )


def _uuid() -> str:
    return uuid4().hex
