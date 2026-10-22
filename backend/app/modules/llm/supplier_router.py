"""模型供应商管理路由：CRUD、启用/禁用、连通性测试、设为当前、预置模板。

挂载到 /api/v1（经 app/modules/llm/router.py 的 /llm 前缀路由并入）。
权限模型（ADR-0005）：入口按「角色」放行 platform_admin/admin/tenant_admin，
数据归属在 service 层双校验——租户管理员只能写 tenant_id=本租户 的行，
平台全局行对其只读（仅可设为本租户生效）；越权与不存在统一 404。
所有写操作与 set-active 记审计（action 前缀 supplier_，metadata 不含密钥）。
返回的 Key 一律打码，绝不返回明文。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_db_session, require_roles
from app.core.responses import ok
from app.modules.audit.service import log_audit
from app.modules.llm import supplier_service
from app.modules.llm.supplier_crypto import decrypt_api_key
from app.modules.llm.supplier_schemas import (
    ProviderTemplate,
    SetActiveBody,
    SupplierCreate,
    SupplierOut,
    SupplierProbeModelsBody,
    SupplierTestBody,
    SupplierTestResult,
    SupplierUpdate,
)

router = APIRouter(tags=["llm-suppliers"])

_admin_only = require_roles("platform_admin", "admin", "tenant_admin")


def _key_configured_dep():
    """使用加密功能前的密钥校验：缺失时抛带配置提示的 AppError。"""
    from app.modules.llm.supplier_crypto import get_fernet

    get_fernet()  # 缺失/非法即抛 AppError


def _actor(user: CurrentUser = Depends(_admin_only)) -> tuple[CurrentUser, bool]:
    """入口上下文：当前用户 + 是否平台管理员（归属双校验下沉 service 层执行）。"""
    return user, ("platform_admin" in user.roles)


def _audit(
    db: Session, user: CurrentUser, action: str, supplier_id: str | None, meta: dict
) -> None:
    """供应商管理审计：metadata 只放 supplier_id/name/provider_type，绝不含密钥材料。"""
    log_audit(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        action=action,
        resource_type="supplier",
        resource_id=supplier_id,
        result="success",
        request_id=user.request_id,
        metadata=meta,
    )


def _meta_of(out):
    return {"supplier_id": out.id, "name": out.name, "provider_type": out.provider_type}


@router.get("/providers", response_model=None)
def list_providers(
    ctx=Depends(_actor),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    user, is_platform_admin = ctx
    items = supplier_service.list_suppliers(db, user.tenant_id, is_platform_admin=is_platform_admin)
    return ok([s.model_dump() for s in items])


@router.post("/providers", response_model=None)
def create_provider(
    payload: SupplierCreate,
    ctx=Depends(_actor),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    user, is_platform_admin = ctx
    try:
        # 归属强制取登录租户上下文（scope=global 仅平台管理员，service 层把关）
        out: SupplierOut = supplier_service.create_supplier(
            db, user.tenant_id, payload, is_platform_admin=is_platform_admin
        )
        _audit(db, user, "supplier_create", out.id, _meta_of(out))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(out.model_dump())


@router.put("/providers/{supplier_id}", response_model=None)
def update_provider(
    supplier_id: str,
    payload: SupplierUpdate,
    ctx=Depends(_actor),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    user, is_platform_admin = ctx
    try:
        out = supplier_service.update_supplier(
            db, user.tenant_id, supplier_id, payload, is_platform_admin=is_platform_admin
        )
        _audit(db, user, "supplier_update", out.id, _meta_of(out))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(out.model_dump())


@router.delete("/providers/{supplier_id}", response_model=None)
def delete_provider(
    supplier_id: str,
    ctx=Depends(_actor),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    user, is_platform_admin = ctx
    try:
        supplier_service.delete_supplier(
            db, user.tenant_id, supplier_id, is_platform_admin=is_platform_admin
        )
        _audit(db, user, "supplier_delete", supplier_id, {"supplier_id": supplier_id})
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok({"deleted": supplier_id})


@router.post("/providers/{supplier_id}/enable", response_model=None)
def enable_provider(
    supplier_id: str,
    ctx=Depends(_actor),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    from app.modules.llm.supplier_schemas import STATUS_ENABLED

    user, is_platform_admin = ctx
    try:
        out = supplier_service.set_supplier_status(
            db, user.tenant_id, supplier_id, STATUS_ENABLED, is_platform_admin=is_platform_admin
        )
        _audit(db, user, "supplier_enable", out.id, _meta_of(out))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(out.model_dump())


@router.post("/providers/{supplier_id}/disable", response_model=None)
def disable_provider(
    supplier_id: str,
    ctx=Depends(_actor),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    from app.modules.llm.supplier_schemas import STATUS_DISABLED

    user, is_platform_admin = ctx
    try:
        out = supplier_service.set_supplier_status(
            db, user.tenant_id, supplier_id, STATUS_DISABLED, is_platform_admin=is_platform_admin
        )
        _audit(db, user, "supplier_disable", out.id, _meta_of(out))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(out.model_dump())


@router.post("/providers/test", response_model=None)
async def test_provider_candidate(
    payload: SupplierTestBody,
    _user=Depends(_admin_only),
):
    """未落库候选配置的连通性测试：key 只在内存用一次，不落库、不打审计。"""
    result: SupplierTestResult = await supplier_service.probe_upstream(
        payload.base_url, payload.api_key, payload.model_id
    )
    return ok(result.model_dump())


@router.post("/providers/probe-models", response_model=None)
async def probe_provider_models(
    payload: SupplierProbeModelsBody,
    ctx=Depends(_actor),
    db=Depends(get_db_session),
):
    """探测上游模型列表（DESIGN-v3 §A）。

    - api_key 优先用请求体传入（只在本请求内存中瞬时使用，不落库/日志/审计）；
    - 缺省时若给了 supplier_id 则用该供应商已存密钥（需在调用者可见范围内）；
    - 两者都没有则不带鉴权头探测（本机 Ollama / LM Studio 常见）。
    """
    user, _is_platform_admin = ctx
    api_key = payload.api_key
    if not api_key and payload.supplier_id:
        row = supplier_service.get_supplier(db, user.tenant_id, payload.supplier_id)
        api_key = decrypt_api_key(row.api_key_enc)
    result = await supplier_service.probe_models(
        provider_type=payload.provider_type,
        base_url=payload.base_url,
        api_key=api_key,
    )
    return ok(result.model_dump())


@router.post("/providers/{supplier_id}/test", response_model=None)
async def test_saved_provider(
    supplier_id: str,
    ctx=Depends(_actor),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    """对已存供应商测试（探测写回结果，与编辑同权），
    结果写回 test_time/response_time_ms/last_error；连续失败 3 次自动禁用。"""
    user, is_platform_admin = ctx
    try:
        out, result = await supplier_service.test_saved_supplier(
            db, user.tenant_id, supplier_id, is_platform_admin=is_platform_admin
        )
        _audit(db, user, "supplier_test", out.id, _meta_of(out))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok({"supplier": out.model_dump(), "result": result.model_dump()})


@router.post("/providers/active", response_model=None)
def set_active_provider(
    payload: SetActiveBody,
    ctx=Depends(_actor),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    """设置本租户当前生效供应商（租户级），即时生效、无需重启；
    租户管理员可将平台全局行设为本租户生效。"""
    user, is_platform_admin = ctx
    if not user.tenant_id:
        from app.core.errors import AppError

        raise AppError("TENANT_REQUIRED", "设置生效供应商需要租户上下文。", http_status=400)
    try:
        out = supplier_service.set_active_supplier(
            db, user.tenant_id, payload.supplier_id, is_platform_admin=is_platform_admin
        )
        _audit(db, user, "supplier_set_active", out.id, _meta_of(out))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(out.model_dump())


@router.get("/provider-templates", response_model=None)
def list_provider_templates(
    _user=Depends(_admin_only),
):
    """预置模板：base_url + 默认 model_id，不含 Key。"""
    templates: list[ProviderTemplate] = supplier_service.get_provider_templates()
    return ok([t.model_dump() for t in templates])
