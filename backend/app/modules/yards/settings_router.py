"""系统设置与角色管理路由（owner/admin）。

说明：本 router 由主程序统一挂载到 /api/v1（本模块不修改全局路由）。
- GET/PUT /yards/current/settings[/{key}]
- GET/PUT/POST/DELETE /yards/current/roles[/{code}]
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import (
    CurrentUser,
    YardContext,
    get_current_user,
    get_db_session,
    get_yard_context,
    require_yard_permission,
)
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.audit.service import log_audit
from app.modules.yards import roles_service, settings_service
from app.modules.yards.authz import (
    can_manage_target_role,
    get_active_membership,
    has_permission,
    is_owner,
)
from app.modules.yards.models import YardRole
from app.modules.yards.permissions import (
    ALL_PERMISSIONS,
    ASSIGNABLE_ROLES,
    GRANTABLE_PERMISSIONS,
    PERMISSION_GROUPS,
    PERMISSION_LABELS,
    PERMISSIONS,
    PROTECTED_PERMISSIONS,
    ROLE_LABELS,
)
from app.modules.yards.schemas import RoleCreateRequest, RoleUpdateRequest

router = APIRouter()

#: 设置项 → 所需权限（注册/安全/技能生成为普通设置，搜索为搜索管理；
#: 自我总结提升涉及成员数据归纳，需保护型权限 settings:manage_protected）
SETTINGS_PERMISSIONS: dict[str, str] = {
    "registration": "settings:manage",
    "security": "settings:manage",
    "search": "search:manage",
    "skill_lab": "settings:manage",
    "self_improve": "settings:manage_protected",
    "agent_generate": "settings:manage",
    "device_analysis": "settings:manage",
    # Agent 自动执行会创建告警/工单/台账，按管理型设置处理
    "farm_agent": "settings:manage",
    # 工作台视图（菜单模式与角色预设）由负责人/管理员统一设置
    "menu": "menu:manage",
    # 语音转写模式影响全小院的音频去向（本机/在线），按管理型设置处理
    "asr": "settings:manage",
    # 指挥官编排（并发/预算/自动建 Agent）属于管理型设置
    "commander": "settings:manage",
}


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "req_unknown")


def _yard_id(user: CurrentUser) -> str:
    if not user.yard_id:
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    return user.yard_id


def _is_owner(db: Session, user: CurrentUser) -> bool:
    if "platform_admin" in user.roles:
        return True
    return is_owner(db, _yard_id(user), user.user_id)


def _require(db: Session, user: CurrentUser, permission: str) -> None:
    if not has_permission(
        db,
        yard_id=_yard_id(user),
        user_id=user.user_id,
        jwt_roles=user.roles,
        permission=permission,
    ):
        raise AppError(
            "FORBIDDEN",
            f"缺少 {permission} 权限，无法执行该操作。",
            http_status=403,
        )


def _actor_role(db: Session, user: CurrentUser, yard_id: str) -> str:
    """当前操作者在本小院的角色码（负责人/平台管理员优先，其次成员关系）。"""
    if "platform_admin" in user.roles:
        return "platform_admin"
    if is_owner(db, yard_id, user.user_id):
        return "yard_owner"
    membership = get_active_membership(db, yard_id, user.user_id)
    if membership is not None:
        return membership.role
    return user.roles[0] if user.roles else ""


def _roles_payload(db: Session, yard_id: str, actor_role: str) -> dict[str, Any]:
    roles = roles_service.list_roles(db, yard_id)
    for role in roles:
        role["can_edit"] = role["code"] != "yard_owner" and can_manage_target_role(
            actor_role, role["code"]
        )
    custom_codes = [str(role["code"]) for role in roles if not role["is_system"]]
    return {
        "roles": roles,
        "permission_groups": list(PERMISSION_GROUPS),
        "permission_labels": PERMISSION_LABELS,
        "protected_permissions": sorted(PROTECTED_PERMISSIONS),
        "grantable_permissions": sorted(GRANTABLE_PERMISSIONS),
        "role_labels": ROLE_LABELS,
        # 成员可分配角色 = 常规系统角色 + 本小院自定义角色
        "assignable_roles": [*ASSIGNABLE_ROLES, *custom_codes],
    }


@router.get("/yards/current/settings")
def get_settings(
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("settings:manage")),
    db: Session = Depends(get_db_session),
):
    yard_id = _yard_id(user)
    data = settings_service.public_view(db, yard_id)
    return ok(
        {**data, **_roles_payload(db, yard_id, _actor_role(db, user, yard_id))},
        request,
    )


@router.get("/yards/current/settings/{key}")
def get_setting(
    key: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """单项设置：返回「默认值 + 已存值」的完整对象（指挥官设置页直接渲染）。"""
    permission = SETTINGS_PERMISSIONS.get(key)
    if permission is None:
        raise AppError("INVALID_REQUEST", f"未知设置项：{key}", http_status=400)
    _require(db, user, permission)
    return ok(settings_service.get_setting(db, _yard_id(user), key), request)


@router.put("/yards/current/settings/{key}")
def update_setting(
    key: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    permission = SETTINGS_PERMISSIONS.get(key)
    if permission is None:
        raise AppError("INVALID_REQUEST", f"未知设置项：{key}", http_status=400)
    _require(db, user, permission)
    yard_id = _yard_id(user)
    if (
        key == "registration"
        and payload.get("default_role") == "yard_admin"
        and not _is_owner(db, user)
    ):
        raise AppError("FORBIDDEN", "只有小院负责人可以设置管理员为默认角色。", http_status=403)
    commander_enabling_publish = False
    if key == "commander":
        current = settings_service.get_setting(db, yard_id, "commander")
        if (
            payload.get("auto_publish_agent") is True
            and current.get("auto_publish_agent") is not True
        ):
            if not _is_owner(db, user):
                raise AppError(
                    "PROTECTED_CONFIRM_REQUIRED",
                    "开启「自动发布新助手」需小院负责人确认。",
                    http_status=403,
                )
            commander_enabling_publish = True
    if key == "self_improve":
        confirm = payload.pop("confirm_auto_publish", False)
        if payload.get("auto_publish") is True and not confirm:
            raise AppError(
                "SELF_IMPROVE_CONFIRM_REQUIRED",
                "开启自动通过知识候选需二次确认（请勾选后再保存）。",
                http_status=400,
            )

    settings_service.update_setting(
        db,
        yard_id=yard_id,
        key=key,
        value=payload,
        actor_id=user.user_id,
        request_id=_request_id(request),
    )
    if commander_enabling_publish:
        log_audit(
            db,
            yard_id=yard_id,
            actor_id=user.user_id,
            action="commander_auto_publish_enabled",
            resource_type="yard_settings",
            resource_id="commander",
            result="success",
            request_id=_request_id(request),
            metadata={"auto_publish_agent": True},
        )
    db.commit()
    if key == "self_improve":
        # 设置变更即时对齐定时任务（创建/暂停/重排期）
        from app.modules.self_improve.service import sync_job

        sync_job(
            db,
            yard_id=yard_id,
            setting=settings_service.get_setting(db, yard_id, "self_improve"),
            actor_id=user.user_id,
        )
        db.commit()
    elif key == "device_analysis":
        # 设备分析定时任务同样随设置即时对齐
        from app.modules.devices.analysis_jobs import sync_job as sync_analysis_job

        sync_analysis_job(
            db,
            yard_id=yard_id,
            setting=settings_service.get_setting(db, yard_id, "device_analysis"),
            actor_id=user.user_id,
        )
        db.commit()
    elif key == "farm_agent":
        # Agent 自动执行：启停/周期即时对齐定时任务；天气配置变了就清缓存
        from app.modules.farm.agent_jobs import sync_job as sync_agent_job
        from app.modules.farm.weather import clear_cache

        clear_cache(yard_id)
        sync_agent_job(
            db,
            yard_id=yard_id,
            setting=settings_service.get_setting(db, yard_id, "farm_agent"),
            actor_id=user.user_id,
        )
        db.commit()
    return ok(settings_service.public_view(db, yard_id), request)


@router.get("/yards/current/roles")
def list_roles(
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("roles:manage")),
    db: Session = Depends(get_db_session),
):
    yard_id = _yard_id(user)
    return ok(_roles_payload(db, yard_id, _actor_role(db, user, yard_id)), request)


def _validate_permissions(
    permissions: list[str],
    *,
    actor_is_owner: bool,
    confirm_protected: bool,
) -> set[str]:
    """校验权限集合；保护型权限只允许负责人二次确认后写入角色。"""
    requested = {str(p) for p in permissions}
    unknown = requested - ALL_PERMISSIONS
    if unknown:
        raise AppError(
            "INVALID_REQUEST",
            f"未知权限点：{', '.join(sorted(unknown))}",
            http_status=400,
        )
    if requested & PROTECTED_PERMISSIONS:
        if not actor_is_owner:
            raise AppError(
                "ROLE_EDIT_FORBIDDEN",
                "保护型权限只能由小院负责人配置。",
                http_status=403,
            )
        if not confirm_protected:
            raise AppError(
                "PROTECTED_CONFIRM_REQUIRED",
                "本次变更包含保护型权限，请二次确认后再保存。",
                http_status=400,
            )
    return requested


def _update_custom_role(
    db: Session,
    row: YardRole,
    req: RoleUpdateRequest,
    *,
    actor_id: str,
    actor_is_owner: bool,
    request_id: str,
) -> dict[str, Any]:
    if req.name is None and req.permissions is None:
        raise AppError("INVALID_REQUEST", "没有需要更新的字段。", http_status=400)
    if req.name is not None:
        clean = req.name.strip()
        if not clean or len(clean) > 64:
            raise AppError("INVALID_REQUEST", "角色名称不能为空且不超过 64 字。", http_status=400)
        row.name = clean
    if req.permissions is not None:
        row.permissions_json = sorted(
            _validate_permissions(
                req.permissions,
                actor_is_owner=actor_is_owner,
                confirm_protected=req.confirm_protected,
            )
        )
    log_audit(
        db,
        yard_id=row.yard_id,
        actor_id=actor_id,
        action="role_updated",
        resource_type="yard_role",
        resource_id=row.code,
        result="success",
        request_id=request_id,
        metadata={"name": row.name, "permissions": row.permissions_json},
    )
    db.flush()
    return {
        "code": row.code,
        "name": row.name,
        "permissions": sorted(str(p) for p in row.permissions_json),
        "is_system": False,
        "is_protected": False,
    }


@router.put("/yards/current/roles/{code}")
def update_role(
    code: str,
    req: RoleUpdateRequest,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("roles:manage")),
    db: Session = Depends(get_db_session),
):
    yard_id = _yard_id(user)
    owner = _is_owner(db, user)
    actor = _actor_role(db, user, yard_id)
    if code == "yard_owner":
        raise AppError("ROLE_EDIT_FORBIDDEN", "负责人权限固定为全集，不能修改。", http_status=403)
    if not can_manage_target_role(actor, code):
        raise AppError(
            "ROLE_EDIT_FORBIDDEN", "无权修改该角色，请由负责人或管理员操作。", http_status=403
        )
    row = db.scalar(select(YardRole).where(YardRole.yard_id == yard_id, YardRole.code == code))
    if row is not None and not row.is_system:
        data = _update_custom_role(
            db,
            row,
            req,
            actor_id=user.user_id,
            actor_is_owner=owner,
            request_id=_request_id(request),
        )
    else:
        if code not in PERMISSIONS:
            raise AppError("ROLE_NOT_FOUND", "角色不存在。", http_status=404)
        permissions = (
            _validate_permissions(
                req.permissions,
                actor_is_owner=owner,
                confirm_protected=req.confirm_protected,
            )
            if req.permissions is not None
            else None
        )
        data = roles_service.update_role(
            db,
            yard_id=yard_id,
            code=code,
            name=req.name,
            permissions=sorted(permissions) if permissions is not None else None,
            actor_id=user.user_id,
            request_id=_request_id(request),
            allow_protected=owner and req.confirm_protected,
        )
    db.commit()
    return ok(data, request)


@router.post("/yards/current/roles", status_code=201)
def create_role(
    req: RoleCreateRequest,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("roles:manage")),
    db: Session = Depends(get_db_session),
):
    permissions = _validate_permissions(
        req.permissions,
        actor_is_owner=_is_owner(db, user),
        confirm_protected=req.confirm_protected,
    )
    data = roles_service.create_custom_role(
        db,
        yard_id=_yard_id(user),
        code=req.code,
        name=req.name,
        permissions=sorted(permissions),
        actor_id=user.user_id,
        request_id=_request_id(request),
        allow_protected=_is_owner(db, user) and req.confirm_protected,
    )
    db.commit()
    return ok(data, request)


@router.delete("/yards/current/roles/{code}")
def delete_role(
    code: str,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("roles:manage")),
    db: Session = Depends(get_db_session),
):
    yard_id = _yard_id(user)
    if not can_manage_target_role(_actor_role(db, user, yard_id), code):
        raise AppError("ROLE_EDIT_FORBIDDEN", "无权删除该角色。", http_status=403)
    roles_service.delete_custom_role(
        db,
        yard_id=yard_id,
        code=code,
        actor_id=user.user_id,
        request_id=_request_id(request),
    )
    db.commit()
    return ok({"code": code, "deleted": True}, request)


# ---------------------------------------------------------------------------
# 工作台视图（菜单模式/角色预设的共享默认值，登录成员都可读取）
# ---------------------------------------------------------------------------


@router.get("/yards/current/menu")
def get_menu_config(
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    return ok(settings_service.get_setting(db, ctx.yard_id, "menu"), request)


@router.put("/yards/current/menu")
def update_menu_config(
    request: Request,
    payload: dict[str, Any] = Body(...),
    user: CurrentUser = Depends(require_yard_permission("menu:manage")),
    db: Session = Depends(get_db_session),
):
    yard_id = _yard_id(user)
    settings_service.update_setting(
        db,
        yard_id=yard_id,
        key="menu",
        value=payload,
        actor_id=user.user_id,
        request_id=_request_id(request),
    )
    db.commit()
    return ok(settings_service.get_setting(db, yard_id, "menu"), request)
