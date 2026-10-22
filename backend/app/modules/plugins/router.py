"""插件路由：列表、扫描、启用/禁用 + v1.3-B workflow 注册/审核/运行/自动建议。"""

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.core.deps import (
    CurrentUser,
    TenantContext,
    get_current_user,
    get_db_session,
    get_tenant_context,
    require_roles,
)
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.plugins import service, workflow
from app.modules.plugins.models import PluginManifest
from app.modules.plugins.schemas import (
    PluginOut,
    PluginRegister,
    PluginReviewIn,
    PluginRunIn,
    PluginRunOut,
    ScanReport,
)

router = APIRouter(prefix="/plugins", tags=["plugins"])

_admin_only = require_roles("platform_admin", "admin")
#: workflow 闸门：tenant_admin（require_roles 内置 platform_admin 直通）
_wf_admin_gate = require_roles("tenant_admin")


def _visible_to(record: PluginManifest, user: CurrentUser) -> bool:
    """列表可见性：全局插件人人可见；租户级 workflow 仅本租户/平台管理员可见。"""
    if record.tenant_id is None or record.tenant_id == user.tenant_id:
        return True
    return "platform_admin" in (user.roles or [])


@router.get("")
def list_plugins(
    plugin_type: str | None = Query(
        default=None, description="workflow|knowledge_pack|domain_pack"
    ),
    status: str | None = Query(
        default=None, description="enabled|disabled|pending_review|rejected"
    ),
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db_session),
):
    records = service.list_plugins(db, plugin_type=plugin_type, status=status)
    visible = [r for r in records if _visible_to(r, user)]
    return ok([PluginOut(**service.plugin_to_out(r)).model_dump(mode="json") for r in visible])


@router.get("/{plugin_id}")
def get_plugin_detail(
    plugin_id: str,
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db_session),
):
    """插件详情（含 manifest 正文，审核人看 steps 用）。

    解析顺序：先按当前租户找 workflow 记录，未命中回落全局插件；
    租户级记录仅本租户/平台管理员可见（跨租户与不存在同样 404，防枚举）。
    """
    record: PluginManifest | None = None
    if user.tenant_id:
        record = workflow.find_tenant_plugin_or_none(db, user.tenant_id, plugin_id)
    if record is None:
        record = (
            db.query(PluginManifest)
            .filter(PluginManifest.plugin_id == plugin_id, PluginManifest.tenant_id.is_(None))
            .first()
        )
    if record is None or not _visible_to(record, user):
        raise AppError(
            "PLUGIN_NOT_FOUND",
            f"插件不存在或无权访问：{plugin_id}",
            http_status=404,
            details={"plugin_id": plugin_id},
        )
    payload = service.plugin_to_out(record)
    payload["manifest"] = record.manifest_json
    payload["checksum"] = record.checksum
    return ok(payload)


@router.post("/scan")
def scan_plugins(
    _user=Depends(_admin_only),
    db=Depends(get_db_session),
):
    report = service.scan_plugins(db)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(ScanReport(**report).model_dump(mode="json"))


@router.post("/{plugin_id}/enable")
def enable_plugin(
    plugin_id: str,
    _user=Depends(_admin_only),
    db=Depends(get_db_session),
):
    record = service.enable_plugin(db, plugin_id)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(PluginOut(**service.plugin_to_out(record)).model_dump(mode="json"))


@router.post("/{plugin_id}/disable")
def disable_plugin(
    plugin_id: str,
    _user=Depends(_admin_only),
    db=Depends(get_db_session),
):
    record = service.disable_plugin(db, plugin_id)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(PluginOut(**service.plugin_to_out(record)).model_dump(mode="json"))


# ---------------------------------------------------------------- v1.3-B workflow 声明式插件
# 双闸口径：register/suggest 只产 pending_review 草稿；review approve 才能 run；
# run 内部 steps 的 tool 一律走 tool_registry.execute（风险闸/逐次审批零绕过）。


@router.post("/register", status_code=201)
def register_workflow_plugin(
    body: PluginRegister,
    ctx: TenantContext = Depends(get_tenant_context),
    _user=Depends(_wf_admin_gate),
    db=Depends(get_db_session),
):
    """注册声明式 workflow（tenant_admin）：校验步骤/工具 → pending_review 草稿。"""
    record = workflow.register_workflow(db, ctx, body.manifest)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return JSONResponse(
        status_code=201,
        content=ok(PluginOut(**service.plugin_to_out(record)).model_dump(mode="json")),
    )


@router.post("/suggest", status_code=201)
def suggest_plugins(
    ctx: TenantContext = Depends(get_tenant_context),
    _user=Depends(_wf_admin_gate),
    db=Depends(get_db_session),
):
    """挖掘近 7 天重复工具序列 → 自动 workflow 草稿（只走审核，绝不自动启用）。"""
    records = workflow.suggest_workflows(db, ctx)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return JSONResponse(
        status_code=201,
        content=ok(
            {
                "created": len(records),
                "plugins": [
                    PluginOut(**service.plugin_to_out(r)).model_dump(mode="json") for r in records
                ],
            }
        ),
    )


@router.post("/{plugin_id}/review")
def review_workflow_plugin(
    plugin_id: str,
    body: PluginReviewIn,
    ctx: TenantContext = Depends(get_tenant_context),
    _user=Depends(_wf_admin_gate),
    db=Depends(get_db_session),
):
    """发布闸：approve → enabled；reject → rejected（tenant_admin/manual 审核）。"""
    record = workflow.review_workflow(db, ctx, plugin_id, body.decision)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(PluginOut(**service.plugin_to_out(record)).model_dump(mode="json"))


@router.post("/{plugin_id}/run")
async def run_workflow_plugin(
    plugin_id: str,
    body: PluginRunIn,
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    """同步解释执行（enabled 才可跑）→ {outputs, final}；租户成员可跑，风险由工具闸兜底。"""
    result = await workflow.run_workflow(db, ctx, plugin_id, body.params)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(PluginRunOut(**result).model_dump(mode="json"))
