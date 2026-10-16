"""模型供应商管理路由：CRUD、启用/禁用、连通性测试、设为当前、预置模板。

挂载到 /api/v1（经 app/modules/llm/router.py 的 /llm 前缀路由并入）。
所有管理接口仅管理员可操作；返回的 Key 一律打码，绝不返回明文。
"""

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, get_db_session, require_roles
from app.core.responses import ok
from app.modules.llm import supplier_service
from app.modules.llm.supplier_schemas import (
    ProviderTemplate,
    SetActiveBody,
    SupplierCreate,
    SupplierOut,
    SupplierTestBody,
    SupplierTestResult,
    SupplierUpdate,
)

router = APIRouter(tags=["llm-suppliers"])

_admin_only = require_roles("platform_admin", "admin")


def _key_configured_dep():
    """使用加密功能前的密钥校验：缺失时抛带配置提示的 AppError。"""
    from app.modules.llm.supplier_crypto import get_fernet

    get_fernet()  # 缺失/非法即抛 AppError


def _tenant_and_admin(user: CurrentUser = Depends(_admin_only)) -> tuple[str | None, bool]:
    return user.tenant_id, ("platform_admin" in user.roles)


@router.get("/providers", response_model=None)
def list_providers(
    ctx=Depends(_tenant_and_admin),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    tenant_id, _ = ctx
    return ok([s.model_dump() for s in supplier_service.list_suppliers(db, tenant_id)])


@router.post("/providers", response_model=None)
def create_provider(
    payload: SupplierCreate,
    ctx=Depends(_tenant_and_admin),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    tenant_id, is_platform_admin = ctx
    try:
        out: SupplierOut = supplier_service.create_supplier(
            db, tenant_id, payload, is_platform_admin=is_platform_admin
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(out.model_dump())


@router.put("/providers/{supplier_id}", response_model=None)
def update_provider(
    supplier_id: str,
    payload: SupplierUpdate,
    ctx=Depends(_tenant_and_admin),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    tenant_id, _ = ctx
    try:
        out = supplier_service.update_supplier(db, tenant_id, supplier_id, payload)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(out.model_dump())


@router.delete("/providers/{supplier_id}", response_model=None)
def delete_provider(
    supplier_id: str,
    ctx=Depends(_tenant_and_admin),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    tenant_id, _ = ctx
    try:
        supplier_service.delete_supplier(db, tenant_id, supplier_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok({"deleted": supplier_id})


@router.post("/providers/{supplier_id}/enable", response_model=None)
def enable_provider(
    supplier_id: str,
    ctx=Depends(_tenant_and_admin),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    from app.modules.llm.supplier_schemas import STATUS_ENABLED

    tenant_id, _ = ctx
    try:
        out = supplier_service.set_supplier_status(db, tenant_id, supplier_id, STATUS_ENABLED)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(out.model_dump())


@router.post("/providers/{supplier_id}/disable", response_model=None)
def disable_provider(
    supplier_id: str,
    ctx=Depends(_tenant_and_admin),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    from app.modules.llm.supplier_schemas import STATUS_DISABLED

    tenant_id, _ = ctx
    try:
        out = supplier_service.set_supplier_status(db, tenant_id, supplier_id, STATUS_DISABLED)
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
    """未落库候选配置的连通性测试：key 只在内存用一次，不落库、不打日志。"""
    result: SupplierTestResult = await supplier_service.probe_upstream(
        payload.base_url, payload.api_key, payload.model_id
    )
    return ok(result.model_dump())


@router.post("/providers/{supplier_id}/test", response_model=None)
async def test_saved_provider(
    supplier_id: str,
    ctx=Depends(_tenant_and_admin),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    """对已存供应商测试，结果写回 test_time/response_time_ms/last_error；
    连续失败 3 次自动禁用。"""
    tenant_id, _ = ctx
    try:
        out, result = await supplier_service.test_saved_supplier(db, tenant_id, supplier_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok({"supplier": out.model_dump(), "result": result.model_dump()})


@router.post("/providers/active", response_model=None)
def set_active_provider(
    payload: SetActiveBody,
    ctx=Depends(_tenant_and_admin),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    """设置当前生效供应商（租户级），即时生效、无需重启。"""
    tenant_id, _ = ctx
    if not tenant_id:
        from app.core.errors import AppError

        raise AppError("TENANT_REQUIRED", "设置生效供应商需要租户上下文。", http_status=400)
    try:
        out = supplier_service.set_active_supplier(db, tenant_id, payload.supplier_id)
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
