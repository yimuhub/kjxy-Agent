"""组织架构接口：``/org``（读取 org:read，维护 org:manage）。

所有查询强制小院隔离；写操作统一提交并写审计。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_db_session, require_yard_permission
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.audit.service import log_audit
from app.modules.org import service
from app.modules.org.schemas import (
    OrgPositionCreate,
    OrgPositionUpdate,
    OrgUnitCreate,
    OrgUnitUpdate,
)

router = APIRouter(prefix="/org", tags=["org"])


def _yard_id(user: CurrentUser) -> str:
    if user.yard_id is None:  # require_yard_permission 已保证，防御性兜底
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    return user.yard_id


def _commit(db: Session) -> None:
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


@router.get("/tree")
def get_tree(
    request: Request,
    include_archived: bool = Query(default=False),
    user: CurrentUser = Depends(require_yard_permission("org:read")),
    db: Session = Depends(get_db_session),
):
    return ok(
        service.list_tree(db, _yard_id(user), include_archived=include_archived),
        request,
    )


@router.get("/units/{unit_id}")
def get_unit(
    unit_id: str,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("org:read")),
    db: Session = Depends(get_db_session),
):
    return ok(service.unit_detail(db, _yard_id(user), unit_id), request)


@router.post("/units")
def create_unit(
    body: OrgUnitCreate,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("org:manage")),
    db: Session = Depends(get_db_session),
):
    yard_id = _yard_id(user)
    row = service.create_unit(
        db,
        yard_id=yard_id,
        actor_id=user.user_id,
        name=body.name,
        parent_id=body.parent_id,
        code=body.code,
        kind=body.kind,
        description=body.description,
        sort_order=body.sort_order,
    )
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user.user_id,
        action="org_unit_created",
        resource_type="org_unit",
        resource_id=row.id,
        result="success",
        request_id=user.request_id,
        metadata={"name": row.name, "kind": row.kind, "parent_id": row.parent_id},
    )
    _commit(db)
    return ok(service.unit_detail(db, yard_id, row.id), request)


@router.put("/units/{unit_id}")
def update_unit(
    unit_id: str,
    body: OrgUnitUpdate,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("org:manage")),
    db: Session = Depends(get_db_session),
):
    yard_id = _yard_id(user)
    row = service.update_unit(
        db,
        yard_id=yard_id,
        actor_id=user.user_id,
        unit_id=unit_id,
        changes=body.model_dump(exclude_unset=True),
    )
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user.user_id,
        action="org_unit_updated",
        resource_type="org_unit",
        resource_id=row.id,
        result="success",
        request_id=user.request_id,
        metadata={"name": row.name, "kind": row.kind, "status": row.status},
    )
    _commit(db)
    return ok(service.unit_detail(db, yard_id, row.id), request)


@router.delete("/units/{unit_id}")
def delete_unit(
    unit_id: str,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("org:manage")),
    db: Session = Depends(get_db_session),
):
    yard_id = _yard_id(user)
    service.delete_unit(db, yard_id=yard_id, unit_id=unit_id)
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user.user_id,
        action="org_unit_deleted",
        resource_type="org_unit",
        resource_id=unit_id,
        result="success",
        request_id=user.request_id,
    )
    _commit(db)
    return ok({"deleted": True}, request)


@router.post("/positions")
def create_position(
    body: OrgPositionCreate,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("org:manage")),
    db: Session = Depends(get_db_session),
):
    yard_id = _yard_id(user)
    row = service.create_position(
        db,
        yard_id=yard_id,
        actor_id=user.user_id,
        org_unit_id=body.org_unit_id,
        title=body.title,
        responsibilities=body.responsibilities,
        headcount=body.headcount,
        agent_id=body.agent_id,
        sort_order=body.sort_order,
    )
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user.user_id,
        action="org_position_created",
        resource_type="org_position",
        resource_id=row.id,
        result="success",
        request_id=user.request_id,
        metadata={"title": row.title, "org_unit_id": row.org_unit_id},
    )
    _commit(db)
    return ok(service.position_detail(db, yard_id, row.id), request)


@router.put("/positions/{position_id}")
def update_position(
    position_id: str,
    body: OrgPositionUpdate,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("org:manage")),
    db: Session = Depends(get_db_session),
):
    yard_id = _yard_id(user)
    row = service.update_position(
        db,
        yard_id=yard_id,
        actor_id=user.user_id,
        position_id=position_id,
        changes=body.model_dump(exclude_unset=True),
    )
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user.user_id,
        action="org_position_updated",
        resource_type="org_position",
        resource_id=row.id,
        result="success",
        request_id=user.request_id,
        metadata={"title": row.title, "org_unit_id": row.org_unit_id},
    )
    _commit(db)
    return ok(service.position_detail(db, yard_id, row.id), request)


@router.delete("/positions/{position_id}")
def delete_position(
    position_id: str,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("org:manage")),
    db: Session = Depends(get_db_session),
):
    yard_id = _yard_id(user)
    service.delete_position(db, yard_id=yard_id, position_id=position_id)
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user.user_id,
        action="org_position_deleted",
        resource_type="org_position",
        resource_id=position_id,
        result="success",
        request_id=user.request_id,
    )
    _commit(db)
    return ok({"deleted": True}, request)
