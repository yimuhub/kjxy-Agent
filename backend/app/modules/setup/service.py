"""系统初始化服务（v3.74）：全新安装的一次性引导。

安全闸：``is_initialized`` 读 ``setup_state`` 单行表；存量库（已有用户但无状态
行）按已初始化处理，避免升级后被重新弹向导。向导只在**从未初始化**的库可用，
完成后端点永久 409。

v3.74 起桌面版不再预置任何账号：管理员、小院、内置技能与模型都由本向导创建。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import create_access_token
from app.modules.auth.models import User
from app.modules.auth.repository import create_user
from app.modules.branding.service import update_settings as update_branding
from app.modules.setup.models import SetupState
from app.modules.yards.models import Yard
from app.modules.yards.repository import add_membership
from app.modules.yards.settings_service import update_setting

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")
WIZARD_VERSION = "3.74"

#: 安装期拒绝的弱账号名：名字即超管的口子必须堵死
WEAK_USERNAMES = frozenset(
    {
        "admin",
        "root",
        "system",
        "administrator",
        "platform_admin",
        "superadmin",
        "user",
        "yard_admin",
        "test",
        "guest",
    }
)

#: 安装期拒绝的常见弱口令（与前端清单保持一致）
WEAK_PASSWORDS = frozenset(
    {
        "admin123",
        "admin1234",
        "admin12345",
        "yard123",
        "demo123",
        "password",
        "password1",
        "12345678",
        "123456789",
        "1234567890",
        "11111111",
        "qwerty",
        "abc12345",
        "kjxy1234",
    }
)


def _setup_state(db: Session) -> SetupState | None:
    return db.scalars(select(SetupState).order_by(SetupState.created_at.asc())).first()


def is_initialized(db: Session) -> bool:
    """已完成初始化：有状态行，或存量库已经有用户（向后兼容）。"""
    if _setup_state(db) is not None:
        return True
    return int(db.scalar(select(func.count()).select_from(User)) or 0) > 0


def _validate_username(db: Session, username: str) -> str:
    username = username.strip()
    if not USERNAME_RE.match(username):
        raise AppError(
            "SETUP_INVALID_USERNAME",
            "登录账号需为 3-32 位字母、数字或下划线。",
            http_status=422,
            details={"field": "username"},
        )
    if username.lower() in WEAK_USERNAMES:
        raise AppError(
            "SETUP_WEAK_USERNAME",
            "该账号名过于常见，容易被猜中，请换一个专有账号。",
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
    return username


def _validate_password(password: str, *, username: str = "") -> None:
    """安装期口令规则：≥10 位、字母+数字、不在弱口令表、不含账号名。"""
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
    if password.lower() in WEAK_PASSWORDS:
        raise AppError(
            "SETUP_WEAK_PASSWORD",
            "该口令过于常见，请换一个。",
            http_status=422,
            details={"field": "password"},
        )
    if username and username.lower() in password.lower():
        raise AppError(
            "SETUP_WEAK_PASSWORD",
            "口令不能包含登录账号。",
            http_status=422,
            details={"field": "password"},
        )


def _ensure_contact_free(db: Session, *, email: str | None, phone: str | None) -> None:
    if email:
        if db.scalar(select(User.id).where(User.email == email)) is not None:
            raise AppError("EMAIL_TAKEN", "该邮箱已被使用。", 409, details={"field": "email"})
    if phone:
        if db.scalar(select(User.id).where(User.phone == phone)) is not None:
            raise AppError("PHONE_TAKEN", "该手机号已被使用。", 409, details={"field": "phone"})


# ---------------------------------------------------------------------------
# 内置技能：与 seed / 自助建院共用同一套落库逻辑
# ---------------------------------------------------------------------------


def builtin_skill_catalog() -> list[dict[str, Any]]:
    """向导可勾选的内置技能清单（纯数据，不查库）。"""
    from app.modules.growth.skills import builtin_items as growth_items
    from app.modules.market.agri_skills import builtin_items as agri_items
    from app.modules.market.office_skills import builtin_items as office_items

    catalog: list[dict[str, Any]] = []
    for pack, items in (
        ("农技", agri_items()),
        ("增长与服务", growth_items()),
        ("办公", office_items()),
    ):
        for item in items:
            catalog.append(
                {
                    "code": str(item["code"]),
                    "name": str(item["name"]),
                    "description": str(item.get("description") or ""),
                    "pack": pack,
                }
            )
    return catalog


def _seed_skills(
    db: Session,
    *,
    yard_id: str,
    actor_id: str,
    request_id: str,
    skill_codes: list[str] | None,
) -> int:
    """按勾选铺设内置技能；未勾选（或全为空）时铺设全部。"""
    from app.modules.growth.skills import builtin_items as growth_items
    from app.modules.market.agri_skills import builtin_items as agri_items
    from app.modules.market.builtin_seed import ensure_skills
    from app.modules.market.office_skills import builtin_items as office_items

    items: list[dict[str, Any]] = [*agri_items(), *growth_items(), *office_items()]
    selected = {str(code) for code in (skill_codes or []) if str(code).strip()}
    if selected:
        items = [item for item in items if str(item["code"]) in selected]
    if not items:
        return 0
    return ensure_skills(
        db,
        items,
        yard_id=yard_id,
        actor_id=actor_id,
        request_id=request_id,
        source_label="setup_wizard",
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
    phone: str | None = None,
    # 第 3 步：小院
    yard_name: str,
    province: str = "",
    city: str = "",
    county: str = "",
    profile_types: list[str] | None = None,
    yard_description: str = "",
    # 第 4 步：能力与模型
    skill_codes: list[str] | None = None,
    llm_provider: dict[str, Any] | None = None,
    # 第 5 步：品牌
    app_name: str = "",
    primary_color: str = "",
    # 功能开关
    search_enabled: bool = True,
    agent_generate_enabled: bool = True,
    agent_generate_daily_limit: int = 10,
    deployment: str = "desktop",
) -> dict[str, Any]:
    """执行一次性初始化：超管 + 首个小院 + 技能 + 画像/品牌/开关，单事务。"""
    if is_initialized(db):
        raise AppError(
            "SETUP_ALREADY_DONE",
            "系统已完成初始化，向导不可再次进入。",
            http_status=409,
        )

    username = _validate_username(db, username)
    _validate_password(password, username=username)
    email = (email or "").strip().lower() or None
    phone = (phone or "").strip() or None
    _ensure_contact_free(db, email=email, phone=phone)

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
        email=email,
        display_name=display_name.strip() or username,
        status="active",
    )
    user.platform_admin = True
    if phone:
        user.phone = phone
    db.flush()

    # ② 首个小院 + 负责人成员关系
    yard = Yard(
        name=yard_name[:128],
        status="active",
        province=(province or None),
        city=(city or None),
        owner_user_id=user.id,
    )
    if county:
        yard.attributes_json = {**(yard.attributes_json or {}), "county": county[:64]}
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
        value={"types": types, "deployment": deployment, "county": county},
        actor_id=user.id,
        request_id=request_id,
    )
    if yard_description:
        yard.scene_type = yard_description[:64]

    # ④ 内置技能（按勾选铺设；不传则全量）
    skill_count = _seed_skills(
        db,
        yard_id=yard.id,
        actor_id=user.id,
        request_id=request_id,
        skill_codes=skill_codes,
    )

    # ⑤ 模型接入（可选；Key 加密保存，明文不落盘）
    model_configured = False
    if llm_provider and (llm_provider.get("api_key") or "").strip():
        model_configured = _create_provider(db, yard.id, llm_provider)

    # ⑥ 品牌与外观（复用平台品牌包；名称/主色，Logo 进入系统后上传）
    branding_payload: dict[str, Any] = {}
    if app_name.strip():
        branding_payload["app_name"] = app_name.strip()[:40]
    if primary_color.strip():
        branding_payload["primary_color"] = primary_color.strip()
    if branding_payload:
        update_branding(db, actor_id=user.id, request_id=request_id, payload=branding_payload)

    # ⑦ 功能开关（只写用户显式选择的，其余用默认）
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
    # ⑧ 初始化状态（单行；此后 /setup/initialize 永久 409）
    db.add(
        SetupState(
            initialized_at=datetime.now(UTC),
            admin_user_id=user.id,
            yard_id=yard.id,
            wizard_version=WIZARD_VERSION,
        )
    )
    db.flush()

    log_audit_setup(db, yard_id=yard.id, actor_id=user.id, username=username)

    token = create_access_token(
        user_id=user.id,
        yard_id=yard.id,
        roles=["platform_admin", "yard_owner"],
        token_version=int(user.token_version or 0),
    )
    return {
        "user_id": user.id,
        "yard_id": yard.id,
        "yard_name": yard.name,
        "username": user.username,
        "skill_count": skill_count,
        "model_configured": model_configured,
        "access_token": token,
    }


_PROVIDER_TYPE_MAP = {
    "deepseek": "deepseek",
    "qwen": "qwen",
    "dashscope": "qwen",
    "moonshot": "moonshot",
    "kimi": "moonshot",
    "openai": "openai",
    "zhipu": "openai_compatible",
    "glm": "openai_compatible",
    "openai_compatible": "openai_compatible",
    "custom": "openai_compatible",
}


def _create_provider(db: Session, yard_id: str, payload: dict[str, Any]) -> bool:
    """把向导里填写的云端供应商真正写入并设为生效；失败整体回滚。"""
    from app.modules.llm import supplier_service
    from app.modules.llm.supplier_schemas import SupplierCreate

    base_url = str(payload.get("base_url") or "").strip()
    model_id = str(payload.get("model_id") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    if not base_url or not model_id or not api_key:
        return False
    supplier_type = _PROVIDER_TYPE_MAP.get(
        str(payload.get("provider_type") or "").strip().lower(), "openai_compatible"
    )
    data = SupplierCreate(
        name=(str(payload.get("name") or "").strip() or "云端模型")[:40],
        provider_type=supplier_type,  # type: ignore[arg-type]
        base_url=base_url,
        model_id=model_id,
        api_key=api_key,
    )
    created = supplier_service.create_supplier(db, yard_id, data, is_platform_admin=True)
    supplier_service.set_active_supplier(db, yard_id, created.id, is_platform_admin=True)
    return True


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
        metadata={"username": username, "via": "setup_wizard", "wizard_version": WIZARD_VERSION},
    )
