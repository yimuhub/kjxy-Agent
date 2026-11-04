"""模型供应商管理路由：CRUD、启用/禁用、连通性测试、设为当前、预置模板。

挂载到 /api/v1（经 app/modules/llm/router.py 的 /llm 前缀路由并入）。
权限模型（ADR-0005）：入口按「角色」放行 platform_admin/admin/yard_admin，
数据归属在 service 层双校验——小院管理员只能写 yard_id=本小院 的行，
平台全局行对其只读（仅可设为本小院生效）；越权与不存在统一 404。
所有写操作与 set-active 记审计（action 前缀 supplier_，metadata 不含密钥）。
返回的 Key 一律打码，绝不返回明文。
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_db_session, require_roles, require_yard_permission
from app.core.responses import ok
from app.modules.audit.service import log_audit
from app.modules.llm import supplier_service
from app.modules.llm.supplier_crypto import decrypt_api_key
from app.modules.llm.supplier_schemas import (
    ProviderTemplate,
    SetActiveBody,
    SupplierCreate,
    SupplierImportBundle,
    SupplierImportUrlBody,
    SupplierModelCreate,
    SupplierModelDetectBody,
    SupplierModelOut,
    SupplierModelsBatchTestBody,
    SupplierModelTestResult,
    SupplierModelUpdate,
    SupplierOut,
    SupplierProbeModelsBody,
    SupplierTestBody,
    SupplierTestResult,
    SupplierUpdate,
)

router = APIRouter(tags=["llm-suppliers"])

_admin_only = require_roles("platform_admin", "yard_admin", "yard_owner")


def _key_configured_dep():
    """使用加密功能前的密钥校验：缺失时抛带配置提示的 AppError。"""
    from app.modules.llm.supplier_crypto import get_fernet

    get_fernet()  # 缺失/非法即抛 AppError


def _list_actor(
    user: CurrentUser = Depends(require_yard_permission("chat:use")),
) -> tuple[CurrentUser, bool]:
    """列表读取：能对话/建智能体的角色即可（写操作仍 _admin_only）。"""
    return user, "platform_admin" in (user.roles or [])


def _actor(user: CurrentUser = Depends(_admin_only)) -> tuple[CurrentUser, bool]:
    """入口上下文：当前用户 + 是否平台管理员（归属双校验下沉 service 层执行）。"""
    return user, ("platform_admin" in user.roles)


def _audit(
    db: Session, user: CurrentUser, action: str, supplier_id: str | None, meta: dict
) -> None:
    """供应商管理审计：metadata 只放 supplier_id/name/provider_type，绝不含密钥材料。"""
    log_audit(
        db,
        yard_id=user.yard_id,
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
    # 读放宽到 chat:use（v3.65）：专家/成员在编辑智能体时需要选模型（Key 永远掩码）；
    # 写操作仍走 _admin_only
    ctx=Depends(_list_actor),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    user, is_platform_admin = ctx
    items = supplier_service.list_suppliers(db, user.yard_id, is_platform_admin=is_platform_admin)
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
        # 归属强制取登录小院上下文（scope=global 仅平台管理员，service 层把关）
        out: SupplierOut = supplier_service.create_supplier(
            db, user.yard_id, payload, is_platform_admin=is_platform_admin
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
            db, user.yard_id, supplier_id, payload, is_platform_admin=is_platform_admin
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
            db, user.yard_id, supplier_id, is_platform_admin=is_platform_admin
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
            db, user.yard_id, supplier_id, STATUS_ENABLED, is_platform_admin=is_platform_admin
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
            db, user.yard_id, supplier_id, STATUS_DISABLED, is_platform_admin=is_platform_admin
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
        payload.base_url, payload.api_key, payload.model_id, provider_type=payload.provider_type
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
        row = supplier_service.get_supplier(db, user.yard_id, payload.supplier_id)
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
            db, user.yard_id, supplier_id, is_platform_admin=is_platform_admin
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
    """设置本小院当前生效供应商（小院级），即时生效、无需重启；
    小院管理员可将平台全局行设为本小院生效。"""
    user, is_platform_admin = ctx
    if not user.yard_id:
        from app.core.errors import AppError

        raise AppError("YARD_REQUIRED", "设置生效供应商需要小院上下文。", http_status=400)
    try:
        out = supplier_service.set_active_supplier(
            db, user.yard_id, payload.supplier_id, is_platform_admin=is_platform_admin
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


# ---------- 供应商多模型目录（v3.33） ----------


def _model_meta(out: SupplierModelOut) -> dict:
    """模型审计 metadata：只放标识与模态，绝不含密钥/单价明细之外的敏感材料。"""
    return {
        "model_id": out.id,
        "model_key": out.model_key,
        "modality": out.modality,
        "protocol": out.protocol,
    }


def _import_response(result, dry_run: bool):
    """dry_run 返回预览（不落库），正式导入返回计数结果。"""
    payload = result.model_dump()
    payload["dry_run"] = dry_run
    return ok(payload)


@router.post("/providers/import", response_model=None)
def import_provider_bundle(
    payload: SupplierImportBundle,
    dry_run: int = Query(default=0, ge=0, le=1),
    ctx=Depends(_actor),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    """导入「新供应商 + 多模型」：dry_run=1 只预览分类，不写库。"""
    user, is_platform_admin = ctx
    try:
        result = supplier_service.import_models(
            db,
            user.yard_id,
            None,
            payload,
            dry_run=bool(dry_run),
            is_platform_admin=is_platform_admin,
        )
        if not dry_run:
            supplier_id = getattr(result, "supplier_id", None)
            _audit(
                db,
                user,
                "supplier_model_import",
                supplier_id,
                {
                    "created": getattr(result, "created", 0),
                    "updated": getattr(result, "updated", 0),
                    "skipped": getattr(result, "skipped", 0),
                },
            )
            db.commit()
    except Exception:
        db.rollback()
        raise
    return _import_response(result, bool(dry_run))


@router.post("/providers/{supplier_id}/models/import", response_model=None)
def import_supplier_models(
    supplier_id: str,
    payload: SupplierImportBundle,
    dry_run: int = Query(default=0, ge=0, le=1),
    ctx=Depends(_actor),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    """向既有供应商导入多模型（JSON/YAML/CSV 文本同样走 content 字段）。"""
    user, is_platform_admin = ctx
    try:
        result = supplier_service.import_models(
            db,
            user.yard_id,
            supplier_id,
            payload,
            dry_run=bool(dry_run),
            is_platform_admin=is_platform_admin,
        )
        if not dry_run:
            _audit(
                db,
                user,
                "supplier_model_import",
                supplier_id,
                {
                    "created": getattr(result, "created", 0),
                    "updated": getattr(result, "updated", 0),
                    "skipped": getattr(result, "skipped", 0),
                },
            )
            db.commit()
    except Exception:
        db.rollback()
        raise
    return _import_response(result, bool(dry_run))


@router.post("/providers/{supplier_id}/models/import-url", response_model=None)
async def import_supplier_models_from_url(
    supplier_id: str,
    payload: SupplierImportUrlBody,
    dry_run: int = Query(default=0, ge=0, le=1),
    ctx=Depends(_actor),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    """按 URL 拉取目录导入：http(s) only、2MB 上限、最多 3 次重定向（每跳 SSRF 校验）。"""
    user, is_platform_admin = ctx
    text = await supplier_service.fetch_import_text(payload.url)
    bundle = SupplierImportBundle(content=text)
    try:
        result = supplier_service.import_models(
            db,
            user.yard_id,
            supplier_id,
            bundle,
            dry_run=bool(dry_run),
            is_platform_admin=is_platform_admin,
        )
        if not dry_run:
            _audit(
                db,
                user,
                "supplier_model_import_url",
                supplier_id,
                {
                    "created": getattr(result, "created", 0),
                    "updated": getattr(result, "updated", 0),
                    "skipped": getattr(result, "skipped", 0),
                },
            )
            db.commit()
    except Exception:
        db.rollback()
        raise
    return _import_response(result, bool(dry_run))


@router.get("/providers/{supplier_id}/models", response_model=None)
def list_supplier_models(
    supplier_id: str,
    ctx=Depends(_actor),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    user, is_platform_admin = ctx
    items = supplier_service.list_supplier_models(
        db, user.yard_id, supplier_id, is_platform_admin=is_platform_admin
    )
    return ok([item.model_dump() for item in items])


@router.post("/providers/{supplier_id}/models/detect-modality", response_model=None)
async def detect_supplier_model_modality(
    supplier_id: str,
    payload: SupplierModelDetectBody,
    ctx=Depends(_actor),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    """自动识别模型模态：先看模型名，再拉上游目录；识别不到交前端手动选择。"""
    user, _is_platform_admin = ctx
    result = await supplier_service.detect_model_modality(
        db,
        user.yard_id,
        supplier_id,
        model_key=payload.model_key,
        upstream_model_id=payload.upstream_model_id,
    )
    return ok(result.model_dump())


@router.post("/providers/{supplier_id}/models", response_model=None)
def create_supplier_model(
    supplier_id: str,
    payload: SupplierModelCreate,
    ctx=Depends(_actor),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    user, is_platform_admin = ctx
    try:
        out = supplier_service.create_supplier_model(
            db, user.yard_id, supplier_id, payload, is_platform_admin=is_platform_admin
        )
        _audit(db, user, "supplier_model_create", supplier_id, _model_meta(out))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(out.model_dump())


@router.patch("/providers/{supplier_id}/models/{model_id}", response_model=None)
def update_supplier_model(
    supplier_id: str,
    model_id: str,
    payload: SupplierModelUpdate,
    ctx=Depends(_actor),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    user, is_platform_admin = ctx
    try:
        out = supplier_service.update_supplier_model(
            db, user.yard_id, supplier_id, model_id, payload, is_platform_admin=is_platform_admin
        )
        _audit(db, user, "supplier_model_update", supplier_id, _model_meta(out))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(out.model_dump())


@router.delete("/providers/{supplier_id}/models/{model_id}", response_model=None)
def delete_supplier_model(
    supplier_id: str,
    model_id: str,
    ctx=Depends(_actor),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    user, is_platform_admin = ctx
    try:
        # 先取一行用于审计展示；删除本身会再次做归属校验
        row = supplier_service.get_supplier_model(db, supplier_id, model_id)
        supplier_service.delete_supplier_model(
            db, user.yard_id, supplier_id, model_id, is_platform_admin=is_platform_admin
        )
        _audit(
            db,
            user,
            "supplier_model_delete",
            supplier_id,
            {"model_id": model_id, "model_key": row.model_key},
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok({"deleted": model_id})


@router.post("/providers/{supplier_id}/models/{model_id}/test", response_model=None)
async def test_supplier_model(
    supplier_id: str,
    model_id: str,
    ctx=Depends(_actor),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    user, is_platform_admin = ctx
    try:
        result: SupplierModelTestResult = await supplier_service.test_supplier_model(
            db, user.yard_id, supplier_id, model_id, is_platform_admin=is_platform_admin
        )
        _audit(
            db,
            user,
            "supplier_model_test",
            supplier_id,
            {"model_id": model_id, "ok": result.ok, "latency_ms": result.latency_ms},
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(result.model_dump())


@router.post("/providers/{supplier_id}/models/batch-test", response_model=None)
async def batch_test_supplier_models(
    supplier_id: str,
    payload: SupplierModelsBatchTestBody | None = None,
    ctx=Depends(_actor),
    db=Depends(get_db_session),
    _key=Depends(_key_configured_dep),
):
    user, is_platform_admin = ctx
    model_ids = payload.model_ids if payload else None
    try:
        results = await supplier_service.test_supplier_models_batch(
            db,
            user.yard_id,
            supplier_id,
            model_ids,
            is_platform_admin=is_platform_admin,
        )
        _audit(
            db,
            user,
            "supplier_model_batch_test",
            supplier_id,
            {"count": len(results), "ok": sum(1 for r in results if r.get("ok"))},
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(results)
