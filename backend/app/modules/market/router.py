"""技能市场 HTTP 接口（B1）。

- GET  /market/catalog：内置种子包目录；risk_level 服务端用 assess_risk 现算
  （不信任种子文件里的任何"自报"），installed 按本租户同码/seed_ 前缀判定。
- POST /market/install：按编码装载种子 → declarative.create_agent +
  publish_agent（种子全低风险，tenant_admin 自批语义直接 published）；
  落库编码加 seed_ 前缀，已存在同码一律 skipped 不覆盖。

权限：tenant_admin（require_roles 内置 platform_admin 直通——平台级装载
由 admin 带租户上下文走同接口）。归属只认登录上下文，请求体禁带 tenant_id。
"""

from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.orm import Session

from app.core.deps import (
    CurrentUser,
    TenantContext,
    get_db_session,
    get_tenant_context,
    require_roles,
)
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.agents import declarative
from app.modules.market.seeds import SEED_AGENTS, SEED_CODE_PREFIX
from app.modules.market.service import (
    MAX_INSTALL_CODES,
    install_seed_codes,
    installed_codes,
)

router = APIRouter(prefix="/market", tags=["market"])

# 租户侧闸门：tenant_admin（platform_admin 直通，ADR-0005 分级审核口径）
_tenant_admin_gate = require_roles("tenant_admin")


def _installed_codes(db: Session, tenant_id: str) -> set[str]:
    """本租户已存在的种子相关编码（含租户自建同码——一律视为已占用）。"""
    return installed_codes(db, tenant_id)


@router.get("/catalog")
def get_catalog(
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    user: CurrentUser = Depends(_tenant_admin_gate),
    db: Session = Depends(get_db_session),
):
    """种子包目录：风险服务端现算，installed 只反映本租户（跨租户不可见）。"""
    installed_codes = _installed_codes(db, ctx.tenant_id)
    items = []
    for seed in SEED_AGENTS:
        code = str(seed["agent_code"])
        items.append(
            {
                "agent_code": code,
                "name": seed["name"],
                "emoji": seed["emoji"],
                "description": seed["description"],
                "domain": seed["domain"],
                "suggested_questions": list(seed["suggested_questions"]),
                # 宁严勿松：不读种子自报，用同一把尺子（assess_risk）现场判定
                "risk_level": declarative.assess_risk(
                    list(seed["allowed_tools"]), list(seed["knowledge_scopes"])
                ),
                "installed": code in installed_codes or SEED_CODE_PREFIX + code in installed_codes,
            }
        )
    return ok({"items": items}, request)


def _parse_install_request(body: dict[str, Any]) -> list[str]:
    """解析并去重 codes；归属字段注入 → 422，其余非法 → 400（与 declarative 口径一致）。"""
    if not isinstance(body, dict):
        raise AppError("INVALID_REQUEST", "请求体必须为 JSON 对象。", http_status=400)
    if "tenant_id" in body:
        raise AppError(
            "VALIDATION_ERROR",
            "请求体不允许携带 tenant_id：租户归属由登录上下文决定。",
            http_status=422,
        )
    codes_raw = body.get("codes")
    if not isinstance(codes_raw, list) or not codes_raw:
        raise AppError("INVALID_REQUEST", "codes 必须为 1-20 个种子编码的数组。", http_status=400)
    if len(codes_raw) > MAX_INSTALL_CODES:
        raise AppError(
            "INVALID_REQUEST", f"单次最多装载 {MAX_INSTALL_CODES} 个种子包。", http_status=400
        )
    codes: list[str] = []
    for raw in codes_raw:
        code = str(raw).strip()
        if not code:
            raise AppError("INVALID_REQUEST", "codes 不允许为空项。", http_status=400)
        if code not in codes:  # 同请求内去重：重复项不会造成重复创建
            codes.append(code)
    return codes


@router.post("/install")
def install_seeds(
    request: Request,
    body: dict[str, Any] = Body(...),
    ctx: TenantContext = Depends(get_tenant_context),
    user: CurrentUser = Depends(_tenant_admin_gate),
    db: Session = Depends(get_db_session),
):
    """装载种子包：逐码 创建→发布→审计→提交；已存在同码 skipped 不覆盖。"""
    codes = _parse_install_request(body)
    result = install_seed_codes(
        db,
        tenant_id=ctx.tenant_id,
        actor_id=user.user_id,
        roles=list(user.roles),
        codes=codes,
        request_id=getattr(request.state, "request_id", ""),
    )
    return ok(result, request)
