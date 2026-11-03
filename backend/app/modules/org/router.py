"""组织架构接口：``/org``（读取 org:read，维护 org:manage）。

所有查询强制小院隔离；写操作统一提交并写审计。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, YardContext, get_db_session, require_yard_permission
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.audit.service import log_audit
from app.modules.org import (  # noqa: F401 - 注册任务 handler
    proposal_service,
    proposal_tasks,
    service,
)
from app.modules.org.models import PROPOSAL_STATUSES, OrgProposal
from app.modules.org.schemas import (
    OrgPositionCreate,
    OrgPositionUpdate,
    OrgProposalApply,
    OrgProposalCreate,
    OrgUnitCreate,
    OrgUnitUpdate,
)
from app.modules.tasks.router import _enqueue_or_inline
from app.modules.tasks.service import TaskService
from app.modules.yards.quota import check_and_consume

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


# ---------------------------------------------------------------------------
# 知识库 → 组织架构提案
# ---------------------------------------------------------------------------


def _proposal_summary(row: OrgProposal) -> dict[str, object]:
    payload = row.proposal_json or {}
    return {
        "id": row.id,
        "title": row.title,
        "status": row.status,
        "summary": payload.get("summary", ""),
        "units": len(payload.get("units") or []),
        "agents": len(payload.get("agent_suggestions") or []),
        "evidence_count": len(row.evidence_json or []),
        "task_id": row.task_id,
        "error_message": row.error_message,
        "created_at": row.created_at,
        "applied_at": row.applied_at,
    }


@router.post("/proposals", status_code=202)
def create_proposal(
    body: OrgProposalCreate,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("org:manage")),
    db: Session = Depends(get_db_session),
):
    """建组织推导任务并立即返回（202）：检索/建模在后台执行。"""
    yard_id = _yard_id(user)
    check_and_consume(db, yard_id, "tasks")
    row = OrgProposal(
        yard_id=yard_id,
        title="",
        instruction=body.instruction.strip(),
        status="generating",
        created_by=user.user_id,
    )
    db.add(row)
    db.flush()
    ctx = YardContext(
        yard_id=yard_id,
        user_id=user.user_id,
        roles=list(user.roles or []),
        request_id=user.request_id,
    )
    task = TaskService.create(
        db,
        ctx,
        task_type=proposal_service.TASK_TYPE,
        input={
            "proposal_id": row.id,
            "instruction": row.instruction,
            "roles": list(user.roles or []),
        },
        request_id=user.request_id,
    )
    row.task_id = task.id
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user.user_id,
        action="org_proposal_requested",
        resource_type="org_proposal",
        resource_id=row.id,
        result="success",
        request_id=user.request_id,
        metadata={"task_id": task.id},
    )
    _commit(db)
    _enqueue_or_inline(db, task)
    return JSONResponse(
        status_code=202,
        content=ok({"proposal_id": row.id, "task_id": task.id}, request),
    )


@router.get("/proposals")
def list_proposals(
    request: Request,
    status: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(require_yard_permission("org:read")),
    db: Session = Depends(get_db_session),
):
    yard_id = _yard_id(user)
    stmt = select(OrgProposal).where(OrgProposal.yard_id == yard_id)
    if status:
        if status not in PROPOSAL_STATUSES:
            raise AppError("INVALID_REQUEST", "status 不合法。", http_status=400)
        stmt = stmt.where(OrgProposal.status == status)
    rows = db.execute(stmt.order_by(OrgProposal.created_at.desc()).limit(limit)).scalars().all()
    healed = False
    for row in rows:
        healed = proposal_service.heal_generating(db, row) or healed
    if healed:
        _commit(db)
    return ok([_proposal_summary(row) for row in rows], request)


@router.get("/proposals/{proposal_id}")
def get_proposal(
    proposal_id: str,
    request: Request,
    parent_id: str | None = Query(default=None, max_length=36),
    user: CurrentUser = Depends(require_yard_permission("org:read")),
    db: Session = Depends(get_db_session),
):
    row = proposal_service.get_proposal(db, _yard_id(user), proposal_id)
    # get_proposal 可能把卡在 generating 的提案同步为 failed，需要落库
    _commit(db)
    return ok(proposal_service.serialize(db, row, parent_id=parent_id), request)


@router.post("/proposals/{proposal_id}/apply")
def apply_proposal(
    proposal_id: str,
    body: OrgProposalApply,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("org:manage")),
    db: Session = Depends(get_db_session),
):
    yard_id = _yard_id(user)
    ctx = YardContext(
        yard_id=yard_id,
        user_id=user.user_id,
        roles=list(user.roles or []),
        request_id=user.request_id,
    )
    result = proposal_service.apply_proposal(
        db, ctx=ctx, proposal_id=proposal_id, parent_id=body.parent_id
    )
    row = proposal_service.get_proposal(db, yard_id, proposal_id)
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user.user_id,
        action="org_proposal_applied",
        resource_type="org_proposal",
        resource_id=proposal_id,
        result="success",
        request_id=user.request_id,
        metadata=result.get("result") or {},
    )
    _commit(db)
    return ok(
        {
            "already_applied": result.get("already_applied"),
            "result": result.get("result"),
            "proposal": proposal_service.serialize(db, row, parent_id=body.parent_id),
        },
        request,
    )


@router.post("/proposals/{proposal_id}/discard")
def discard_proposal(
    proposal_id: str,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("org:manage")),
    db: Session = Depends(get_db_session),
):
    yard_id = _yard_id(user)
    row = proposal_service.discard_proposal(
        db,
        ctx=YardContext(
            yard_id=yard_id,
            user_id=user.user_id,
            roles=list(user.roles or []),
            request_id=user.request_id,
        ),
        proposal_id=proposal_id,
    )
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user.user_id,
        action="org_proposal_discarded",
        resource_type="org_proposal",
        resource_id=proposal_id,
        result="success",
        request_id=user.request_id,
    )
    _commit(db)
    return ok(_proposal_summary(row), request)
