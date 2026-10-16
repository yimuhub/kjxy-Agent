"""插件路由：列表、扫描、启用/禁用。"""

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_current_user, get_db_session, require_roles
from app.core.responses import ok
from app.modules.plugins import service
from app.modules.plugins.schemas import PluginOut, ScanReport

router = APIRouter(prefix="/plugins", tags=["plugins"])

_admin_only = require_roles("platform_admin", "admin")


@router.get("")
def list_plugins(
    plugin_type: str | None = Query(
        default=None, description="workflow|knowledge_pack|domain_pack"
    ),
    status: str | None = Query(default=None, description="enabled|disabled"),
    _user=Depends(get_current_user),
    db=Depends(get_db_session),
):
    records = service.list_plugins(db, plugin_type=plugin_type, status=status)
    return ok([PluginOut(**service.plugin_to_out(r)).model_dump(mode="json") for r in records])


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
