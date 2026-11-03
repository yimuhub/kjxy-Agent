"""工作区接口：``/workspace``（读 workspace:read，改 workspace:write，管理 workspace:manage）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, YardContext, get_db_session, require_yard_permission
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.workspace import service
from app.modules.workspace.schemas import (
    WorkspacePreviewRequest,
    WorkspaceProjectCreate,
    WorkspaceRollbackRequest,
)

router = APIRouter(prefix="/workspace", tags=["workspace"])


def _ctx(user: CurrentUser) -> YardContext:
    if user.yard_id is None:
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    return YardContext(
        yard_id=user.yard_id,
        user_id=user.user_id,
        roles=list(user.roles),
        request_id=user.request_id,
    )


def _commit(db: Session) -> None:
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


@router.get("/status")
def get_status(
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("workspace:read")),
    db: Session = Depends(get_db_session),
):
    return ok(service.status(db, _ctx(user)), request)


@router.get("/projects")
def list_projects(
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("workspace:read")),
    db: Session = Depends(get_db_session),
):
    return ok({"items": service.list_projects(db, _ctx(user))}, request)


@router.post("/projects")
def create_project(
    body: WorkspaceProjectCreate,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("workspace:manage")),
    db: Session = Depends(get_db_session),
):
    result = service.create_project(
        db,
        _ctx(user),
        name=body.name,
        root_path=body.root_path,
        description=body.description,
        allowed_paths=body.allowed_paths,
    )
    _commit(db)
    return ok(result, request)


@router.get("/projects/{project_id}")
def get_project(
    project_id: str,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("workspace:read")),
    db: Session = Depends(get_db_session),
):
    return ok(service.project_detail(db, _ctx(user), project_id), request)


@router.delete("/projects/{project_id}")
def archive_project(
    project_id: str,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("workspace:manage")),
    db: Session = Depends(get_db_session),
):
    result = service.archive_project(db, _ctx(user), project_id)
    _commit(db)
    return ok(result, request)


@router.get("/projects/{project_id}/file")
def read_file(
    project_id: str,
    request: Request,
    path: str = Query(min_length=1, max_length=1024),
    user: CurrentUser = Depends(require_yard_permission("workspace:read")),
    db: Session = Depends(get_db_session),
):
    return ok(service.read_file(db, _ctx(user), project_id, path), request)


@router.get("/projects/{project_id}/changes")
def list_changes(
    project_id: str,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("workspace:read")),
    db: Session = Depends(get_db_session),
):
    return ok({"items": service.list_changes(db, _ctx(user), project_id)}, request)


@router.post("/projects/{project_id}/changes/preview")
def preview_change(
    project_id: str,
    body: WorkspacePreviewRequest,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("workspace:write")),
    db: Session = Depends(get_db_session),
):
    result = service.preview_change(
        db,
        _ctx(user),
        project_id=project_id,
        plugin_id=body.plugin_id,
        path=body.path,
        content=body.content,
    )
    _commit(db)
    return ok(result, request)


@router.post("/changes/{change_id}/apply")
def apply_change(
    change_id: str,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("workspace:write")),
    db: Session = Depends(get_db_session),
):
    result = service.apply_change(db, _ctx(user), change_id)
    _commit(db)
    return ok(result, request)


@router.post("/changes/{change_id}/rollback")
def rollback_change(
    change_id: str,
    body: WorkspaceRollbackRequest,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("workspace:write")),
    db: Session = Depends(get_db_session),
):
    result = service.rollback_change(db, _ctx(user), change_id, force=body.force)
    _commit(db)
    return ok(result, request)
