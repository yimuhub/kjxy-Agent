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
from app.modules.agents.models import CREATED_FROM_TEMPLATE, TenantAgent
from app.modules.audit.service import log_audit
from app.modules.market.seeds import SEED_AGENTS, SEED_CODE_PREFIX, find_seed

router = APIRouter(prefix="/market", tags=["market"])

# 租户侧闸门：tenant_admin（platform_admin 直通，ADR-0005 分级审核口径）
_tenant_admin_gate = require_roles("tenant_admin")

#: 单次装载编码数量上限（防刷：一次最多装完整目录的一半以上即拒绝）
MAX_INSTALL_CODES = 20


def _installed_codes(db: Session, tenant_id: str) -> set[str]:
    """本租户已存在的种子相关编码（含租户自建同码——一律视为已占用）。"""
    candidates: list[str] = []
    for seed in SEED_AGENTS:
        code = str(seed["agent_code"])
        candidates.extend([code, SEED_CODE_PREFIX + code])
    rows = (
        db.query(TenantAgent.agent_code)
        .filter(TenantAgent.tenant_id == tenant_id, TenantAgent.agent_code.in_(candidates))
        .all()
    )
    return {str(r[0]) for r in rows}


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
    installed: list[str] = []
    skipped: list[str] = []
    failed: list[dict[str, str]] = []
    taken = _installed_codes(db, ctx.tenant_id)

    for code in codes:
        seed = find_seed(code)
        if seed is None:
            failed.append(
                {"code": code, "error": "种子包不存在，请从 /market/catalog 获取可用编码。"}
            )
            continue
        if code in taken or SEED_CODE_PREFIX + code in taken:
            # 幂等：重复装载视为成功跳过，绝不覆盖租户已有配置（含租户自建同码）
            skipped.append(code)
            continue
        payload: dict[str, Any] = {
            "agent_code": SEED_CODE_PREFIX + code,
            "name": seed["name"],
            "description": seed["description"],
            "emoji": seed["emoji"],
            "persona_prompt": seed["persona_prompt"],
            "task_prompt_template": seed["task_prompt_template"],
            "allowed_tools": list(seed["allowed_tools"]),
            "knowledge_scopes": list(seed["knowledge_scopes"]),
            "created_from": CREATED_FROM_TEMPLATE,
        }
        try:
            req = declarative.parse_create_request(payload)
            agent = declarative.create_agent(
                db,
                tenant_id=ctx.tenant_id,
                actor_id=user.user_id,
                req=req,
                request_id=ctx.request_id,
            )
            # 种子全低风险 → tenant_admin 自批语义直接 published（走既有分级发布闸门）
            declarative.publish_agent(
                db,
                tenant_id=ctx.tenant_id,
                actor_id=user.user_id,
                agent_id=agent.id,
                roles=list(user.roles),
                request_id=ctx.request_id,
            )
            log_audit(
                db,
                tenant_id=ctx.tenant_id,
                actor_id=user.user_id,
                action="market_install",
                resource_type="tenant_agent",
                resource_id=agent.id,
                result="success",
                request_id=ctx.request_id,
                metadata={
                    "seed_code": code,
                    "installed_as": agent.agent_code,
                    "domain": seed["domain"],
                },
            )
            db.commit()
            installed.append(code)
            taken.add(agent.agent_code)
        except AppError as e:
            # 单码失败回滚该码（此前已提交的码不受影响），继续处理其余编码
            db.rollback()
            failed.append({"code": code, "error": e.message})
    return ok({"installed": installed, "skipped": skipped, "failed": failed}, request)
