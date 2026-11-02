"""多集群节点接口（平台管理员）：登记、探测与版本一致性体检。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, get_db_session
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.audit.service import log_audit
from app.modules.cluster import service

router = APIRouter(prefix="/cluster", tags=["cluster"])


class ClusterNodeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=60)
    base_url: str = Field(min_length=1, max_length=300)
    note: str | None = Field(default=None, max_length=200)


class ClusterNodeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str | None = Field(default=None, min_length=1, max_length=60)
    base_url: str | None = Field(default=None, min_length=1, max_length=300)
    note: str | None = Field(default=None, max_length=200)


def _require_platform(user: CurrentUser) -> None:
    if "platform_admin" not in user.roles:
        raise AppError("FORBIDDEN", "仅平台管理员可管理集群节点。", http_status=403)


def _summary(items: list[dict[str, object]]) -> dict[str, int]:
    return {
        "total": len(items),
        "online": sum(1 for item in items if item.get("status") == "online"),
        "degraded": sum(1 for item in items if item.get("status") == "degraded"),
        "offline": sum(1 for item in items if item.get("status") == "offline"),
        "never": sum(1 for item in items if item.get("status") == "never"),
    }


@router.get("/nodes")
def list_nodes(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    _require_platform(user)
    items = [service.node_out(row) for row in service.list_nodes(db)]
    return ok({"items": items, "summary": _summary(items)}, request)


@router.post("/nodes")
def create_node(
    body: ClusterNodeCreate,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    _require_platform(user)
    row = service.create_node(
        db,
        name=body.name,
        base_url=body.base_url,
        note=body.note,
        created_by=user.user_id,
    )
    log_audit(
        db,
        yard_id=user.yard_id,
        actor_id=user.user_id,
        action="cluster_node_created",
        resource_type="cluster_node",
        resource_id=row.id,
        result="success",
        request_id=user.request_id,
        metadata={"name": row.name},
    )
    db.commit()
    return ok({"node": service.node_out(row)}, request)


@router.put("/nodes/{node_id}")
def update_node(
    node_id: str,
    body: ClusterNodeUpdate,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    _require_platform(user)
    row = service.update_node(
        db,
        node_id,
        name=body.name,
        base_url=body.base_url,
        note=body.note,
    )
    log_audit(
        db,
        yard_id=user.yard_id,
        actor_id=user.user_id,
        action="cluster_node_updated",
        resource_type="cluster_node",
        resource_id=row.id,
        result="success",
        request_id=user.request_id,
        metadata={"name": row.name},
    )
    db.commit()
    return ok({"node": service.node_out(row)}, request)


@router.delete("/nodes/{node_id}")
def delete_node(
    node_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    _require_platform(user)
    service.delete_node(db, node_id)
    log_audit(
        db,
        yard_id=user.yard_id,
        actor_id=user.user_id,
        action="cluster_node_deleted",
        resource_type="cluster_node",
        resource_id=node_id,
        result="success",
        request_id=user.request_id,
    )
    db.commit()
    return ok({"deleted": True}, request)


@router.post("/nodes/{node_id}/probe")
def probe_node(
    node_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    _require_platform(user)
    row = service.get_node(db, node_id)
    service.probe_node(
        db,
        row,
        expected_version=request.app.version,
        expected_head=service.migration_head(db),
    )
    log_audit(
        db,
        yard_id=user.yard_id,
        actor_id=user.user_id,
        action="cluster_node_probed",
        resource_type="cluster_node",
        resource_id=row.id,
        result="success",
        request_id=user.request_id,
        metadata={"status": row.status},
    )
    db.commit()
    return ok({"node": service.node_out(row)}, request)


@router.post("/probe-all")
def probe_all(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    _require_platform(user)
    rows, _ = service.probe_all(
        db,
        expected_version=request.app.version,
        expected_head=service.migration_head(db),
    )
    items = [service.node_out(row) for row in rows]
    summary = _summary(items)
    log_audit(
        db,
        yard_id=user.yard_id,
        actor_id=user.user_id,
        action="cluster_nodes_probed",
        resource_type="cluster_node",
        resource_id=None,
        result="success",
        request_id=user.request_id,
        metadata=summary,
    )
    db.commit()
    return ok({"items": items, "summary": summary}, request)
