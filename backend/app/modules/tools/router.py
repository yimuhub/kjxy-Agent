"""可用工具接口：统一目录（内置 + 本小院自定义 + MCP 快照）、详情与增删启停。

读：``GET /tools`` 保持既有数组形状（全员，只回启用项）；``GET /tools/catalog`` 给
「可用工具」页的完整视图（含来源/风险中文说明与停用项，需 ``tools:manage``）。
写：手填新增、编辑、启停、删除与一句话生成，全部要求 ``tools:manage``；
生成结果一律是**未启用草稿**，必须人工确认启用（模型产出不直接对模型可见）。
"""

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, get_db_session, require_yard_permission
from app.core.responses import ok
from app.modules.audit.service import log_audit
from app.modules.tools import catalog_service, generator
from app.modules.tools.models import ToolDefinition
from app.modules.tools.schemas import ToolOut

router = APIRouter(prefix="/tools", tags=["tools"])

_manage = require_yard_permission("tools:manage")


def _to_out(row: ToolDefinition) -> ToolOut:
    return ToolOut(
        code=row.code,
        version=row.version,
        name=row.name,
        description=row.description,
        input_schema=row.input_schema_json,
        output_schema=row.output_schema_json,
        required_permissions=row.required_permissions_json or [],
        risk_level=row.risk_level,
        side_effect=row.side_effect,
        timeout_seconds=row.timeout_seconds,
        enabled=row.enabled,
    )


def _audit(db: Session, user: CurrentUser, action: str, code: str, meta: dict[str, Any]) -> None:
    """工具目录审计：metadata 只放 code/kind/风险等结构信息，绝不含头值或密钥。"""
    log_audit(
        db,
        yard_id=user.yard_id,
        actor_id=user.user_id,
        action=action,
        resource_type="tool",
        resource_id=code,
        result="success",
        request_id=user.request_id,
        metadata=meta,
    )


@router.get("")
def list_tools(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):  # noqa: B008
    """既有契约：本小院当前**可调用**的工具清单（内置启用项 + 自定义启用项 + MCP 快照）。"""
    rows = (
        db.query(ToolDefinition)
        .filter(ToolDefinition.enabled.is_(True))
        .filter(
            (ToolDefinition.yard_id.is_(None)) | (ToolDefinition.yard_id == user.yard_id)
            if user.yard_id
            else ToolDefinition.yard_id.is_(None)
        )
        .order_by(ToolDefinition.code)
        .all()
    )
    return ok([_to_out(r).model_dump() for r in rows])


@router.get("/catalog")
def get_catalog(
    request: Request,
    user: CurrentUser = Depends(_manage),
    db: Session = Depends(get_db_session),
    include_disabled: bool = Query(default=True),
):
    """「可用工具」页视图：三类来源合并 + 分组计数 + 风险/来源中文标签。"""
    payload = catalog_service.list_all(db, user.yard_id or "", include_disabled=include_disabled)
    return ok(payload, request)


@router.post("/generate")
async def generate_tool(
    request: Request,
    payload: dict[str, Any] = Body(...),
    user: CurrentUser = Depends(_manage),
    db: Session = Depends(get_db_session),
):
    """一句话生成工具草稿（**不落库**）：预览确认后再调 ``POST /tools`` 建草稿/启用。"""
    utterance = str(payload.get("utterance") or "").strip()
    result = await generator.generate_spec(
        db,
        yard_id=user.yard_id or "",
        user_id=user.user_id,
        request_id=user.request_id,
        utterance=utterance,
    )
    return ok(result, request)


@router.post("")
def create_tool(
    request: Request,
    payload: dict[str, Any] = Body(...),
    user: CurrentUser = Depends(_manage),
    db: Session = Depends(get_db_session),
):
    """手填/确认后新增自定义工具；``enabled=true`` 表示直接启用（仍需实现完整）。"""
    enabled = bool(payload.pop("enabled", False))
    try:
        item = catalog_service.create_custom(
            db, user.yard_id or "", user.user_id, payload, enabled=enabled
        )
        _audit(
            db,
            user,
            "tool_create",
            item["code"],
            {"impl_kind": item["impl_kind"], "risk_level": item["risk_level"], "enabled": enabled},
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(item, request)


@router.get("/{code}")
def get_tool(
    code: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """工具详情：能做什么、参数表、风险、所需权限、调用示例与最近调用记录。"""
    detail = catalog_service.get_detail(db, user.yard_id or "", code)
    detail["call_example"] = catalog_service.schema_json_hint(detail.get("input_schema") or {})
    return ok(detail, request)


@router.put("/{code}")
def update_tool(
    code: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
    user: CurrentUser = Depends(_manage),
    db: Session = Depends(get_db_session),
):
    try:
        item = catalog_service.update_custom(db, user.yard_id or "", code, payload)
        _audit(db, user, "tool_update", code, {"impl_kind": item["impl_kind"]})
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(item, request)


@router.post("/{code}/enable")
def enable_tool(
    code: str,
    request: Request,
    user: CurrentUser = Depends(_manage),
    db: Session = Depends(get_db_session),
):
    """启用草稿：内置行为平台级开关；自定义行必须已有可执行实现。"""
    try:
        item = catalog_service.set_enabled(db, user.yard_id or "", code, True)
        _audit(db, user, "tool_enable", code, {"source": item["source"]})
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(item, request)


@router.post("/{code}/disable")
def disable_tool(
    code: str,
    request: Request,
    user: CurrentUser = Depends(_manage),
    db: Session = Depends(get_db_session),
):
    try:
        item = catalog_service.set_enabled(db, user.yard_id or "", code, False)
        _audit(db, user, "tool_disable", code, {"source": item["source"]})
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(item, request)


@router.delete("/{code}")
def delete_tool(
    code: str,
    request: Request,
    user: CurrentUser = Depends(_manage),
    db: Session = Depends(get_db_session),
):
    """删除仅限本小院自定义工具；内置工具只能停用（历史审批单与调用记录按 code 引用）。"""
    try:
        catalog_service.delete_custom(db, user.yard_id or "", code)
        _audit(db, user, "tool_delete", code, {})
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok({"deleted": code}, request)
