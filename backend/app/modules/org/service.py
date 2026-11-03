"""组织架构服务：树查询、单元/岗位维护、循环与跨院校验。

所有写操作只 flush，不 commit；由路由统一提交并写审计。
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.agents.models import STATUS_DELETED, YardAgent
from app.modules.org.models import (
    MAX_DEPTH,
    UNIT_KIND_LABELS,
    UNIT_KINDS,
    UNIT_STATUS_ACTIVE,
    UNIT_STATUSES,
    OrgPosition,
    OrgUnit,
)

_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,59}$")


def _not_found(message: str, **details: object) -> AppError:
    return AppError("ORG_UNIT_NOT_FOUND", message, http_status=404, details=dict(details))


def _get_unit(db: Session, yard_id: str, unit_id: str) -> OrgUnit:
    row = db.get(OrgUnit, unit_id)
    if row is None or row.yard_id != yard_id:
        raise _not_found("组织单元不存在或无权访问。", unit_id=unit_id)
    return row


def _get_position(db: Session, yard_id: str, position_id: str) -> OrgPosition:
    row = db.get(OrgPosition, position_id)
    if row is None or row.yard_id != yard_id:
        raise AppError(
            "ORG_POSITION_NOT_FOUND",
            "岗位不存在或无权访问。",
            http_status=404,
            details={"position_id": position_id},
        )
    return row


def _clean_name(value: str) -> str:
    name = (value or "").strip()
    if not name:
        raise AppError("ORG_INVALID", "名称不能为空。", http_status=400)
    return name


def _clean_code(value: str | None) -> str:
    code = (value or "").strip().lower()
    if not code:
        code = f"unit-{uuid.uuid4().hex[:8]}"
    if not _CODE_RE.match(code):
        raise AppError(
            "ORG_INVALID",
            "编码只能包含小写字母、数字、连字符和下划线，且以字母或数字开头。",
            http_status=400,
            details={"code": code},
        )
    return code


def _ensure_agent(db: Session, yard_id: str, agent_id: str | None) -> None:
    if not agent_id:
        return
    row = db.get(YardAgent, agent_id)
    if row is None or row.yard_id != yard_id or row.status == STATUS_DELETED:
        raise AppError(
            "ORG_AGENT_INVALID",
            "绑定的 Agent 不存在或不属于本小院。",
            http_status=400,
            details={"agent_id": agent_id},
        )


def _assert_unique_code(
    db: Session, yard_id: str, code: str, *, exclude_id: str | None = None
) -> None:
    stmt = select(OrgUnit.id).where(OrgUnit.yard_id == yard_id, OrgUnit.code == code)
    if exclude_id:
        stmt = stmt.where(OrgUnit.id != exclude_id)
    if db.scalar(stmt) is not None:
        raise AppError(
            "ORG_CODE_DUPLICATE",
            f"组织编码已存在：{code}",
            http_status=409,
            details={"code": code},
        )


def _validate_parent(
    db: Session, yard_id: str, *, unit_id: str | None, parent_id: str | None
) -> None:
    if parent_id is None:
        return
    parent = _get_unit(db, yard_id, parent_id)
    if unit_id and parent.id == unit_id:
        raise AppError("ORG_CYCLE", "不能把组织单元挂到自己下面。", http_status=400)
    seen: set[str] = {unit_id} if unit_id else set()
    cursor: OrgUnit | None = parent
    depth = 1
    while cursor is not None:
        if cursor.id in seen:
            raise AppError("ORG_CYCLE", "组织层级存在循环引用。", http_status=400)
        seen.add(cursor.id)
        depth += 1
        if depth > MAX_DEPTH:
            raise AppError(
                "ORG_DEPTH_EXCEEDED",
                f"组织层级最多 {MAX_DEPTH} 级。",
                http_status=400,
            )
        cursor = db.get(OrgUnit, cursor.parent_id) if cursor.parent_id else None


def _agents_map(db: Session, yard_id: str, ids: set[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    rows = (
        db.execute(
            select(YardAgent).where(
                YardAgent.yard_id == yard_id,
                YardAgent.id.in_(ids),
                YardAgent.status != STATUS_DELETED,
            )
        )
        .scalars()
        .all()
    )
    return {
        row.id: {
            "id": row.id,
            "agent_code": row.agent_code,
            "name": row.name,
            "emoji": row.emoji,
            "status": row.status,
        }
        for row in rows
    }


def _position_out(row: OrgPosition, agents: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": row.id,
        "org_unit_id": row.org_unit_id,
        "title": row.title,
        "responsibilities": row.responsibilities or "",
        "headcount": row.headcount,
        "agent_id": row.agent_id,
        "agent": agents.get(row.agent_id) if row.agent_id else None,
        "sort_order": row.sort_order,
        "status": row.status,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _unit_out(row: OrgUnit) -> dict[str, Any]:
    return {
        "id": row.id,
        "parent_id": row.parent_id,
        "code": row.code,
        "name": row.name,
        "kind": row.kind,
        "kind_label": UNIT_KIND_LABELS.get(row.kind, row.kind),
        "description": row.description or "",
        "sort_order": row.sort_order,
        "status": row.status,
        "source": row.source,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "children": [],
        "positions": [],
    }


def list_tree(db: Session, yard_id: str, *, include_archived: bool = False) -> dict[str, Any]:
    """返回嵌套组织树 + 扁平列表 + 汇总。"""
    unit_stmt = select(OrgUnit).where(OrgUnit.yard_id == yard_id)
    pos_stmt = select(OrgPosition).where(OrgPosition.yard_id == yard_id)
    if not include_archived:
        unit_stmt = unit_stmt.where(OrgUnit.status == UNIT_STATUS_ACTIVE)
        pos_stmt = pos_stmt.where(OrgPosition.status == UNIT_STATUS_ACTIVE)
    units = list(db.execute(unit_stmt.order_by(OrgUnit.sort_order, OrgUnit.name)).scalars())
    positions = list(
        db.execute(pos_stmt.order_by(OrgPosition.sort_order, OrgPosition.title)).scalars()
    )

    agents = _agents_map(db, yard_id, {p.agent_id for p in positions if p.agent_id})
    nodes: dict[str, dict[str, Any]] = {u.id: _unit_out(u) for u in units}
    for pos in positions:
        node = nodes.get(pos.org_unit_id)
        if node is not None:
            node["positions"].append(_position_out(pos, agents))

    roots: list[dict[str, Any]] = []
    flat: list[dict[str, Any]] = []
    for unit in units:
        node = nodes[unit.id]
        if unit.parent_id and unit.parent_id in nodes:
            nodes[unit.parent_id]["children"].append(node)
        else:
            roots.append(node)

    def walk(node: dict[str, Any], depth: int, prefix: str) -> None:
        path = f"{prefix} / {node['name']}" if prefix else str(node["name"])
        flat.append(
            {
                "id": node["id"],
                "parent_id": node["parent_id"],
                "name": node["name"],
                "kind": node["kind"],
                "kind_label": node["kind_label"],
                "depth": depth,
                "path": path,
            }
        )
        for child in node["children"]:
            walk(child, depth + 1, path)

    for root in roots:
        walk(root, 0, "")

    return {
        "items": roots,
        "flat": flat,
        "summary": {
            "units": len(units),
            "departments": sum(1 for u in units if u.kind in ("department", "team")),
            "positions": len(positions),
            "agents_bound": len({p.agent_id for p in positions if p.agent_id}),
            "roots": len(roots),
        },
        "include_archived": include_archived,
    }


def create_unit(
    db: Session,
    *,
    yard_id: str,
    actor_id: str,
    name: str,
    parent_id: str | None,
    code: str | None,
    kind: str,
    description: str | None,
    sort_order: int,
) -> OrgUnit:
    if kind not in UNIT_KINDS:
        raise AppError("ORG_INVALID", f"不支持的组织类型：{kind}", http_status=400)
    _validate_parent(db, yard_id, unit_id=None, parent_id=parent_id)
    clean_code = _clean_code(code)
    _assert_unique_code(db, yard_id, clean_code)
    row = OrgUnit(
        yard_id=yard_id,
        parent_id=parent_id,
        code=clean_code,
        name=_clean_name(name),
        kind=kind,
        description=(description or "").strip() or None,
        sort_order=sort_order,
        status=UNIT_STATUS_ACTIVE,
        source="manual",
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            "ORG_CODE_DUPLICATE",
            f"组织编码已存在：{clean_code}",
            http_status=409,
            details={"code": clean_code},
        ) from exc
    return row


def update_unit(
    db: Session,
    *,
    yard_id: str,
    actor_id: str,
    unit_id: str,
    changes: dict[str, Any],
) -> OrgUnit:
    row = _get_unit(db, yard_id, unit_id)
    if "name" in changes and changes["name"] is not None:
        row.name = _clean_name(str(changes["name"]))
    if "code" in changes and changes["code"] is not None:
        code = _clean_code(str(changes["code"]))
        _assert_unique_code(db, yard_id, code, exclude_id=row.id)
        row.code = code
    if "kind" in changes and changes["kind"] is not None:
        kind = str(changes["kind"])
        if kind not in UNIT_KINDS:
            raise AppError("ORG_INVALID", f"不支持的组织类型：{kind}", http_status=400)
        row.kind = kind
    if "status" in changes and changes["status"] is not None:
        status = str(changes["status"])
        if status not in UNIT_STATUSES:
            raise AppError("ORG_INVALID", f"不支持的状态：{status}", http_status=400)
        row.status = status
    if "description" in changes:
        row.description = (str(changes["description"] or "")).strip() or None
    if "sort_order" in changes and changes["sort_order"] is not None:
        row.sort_order = int(changes["sort_order"])
    if "parent_id" in changes:
        parent_id = changes["parent_id"]
        _validate_parent(db, yard_id, unit_id=row.id, parent_id=parent_id)
        row.parent_id = parent_id
    row.updated_by = actor_id
    db.flush()
    return row


def delete_unit(db: Session, *, yard_id: str, unit_id: str) -> None:
    row = _get_unit(db, yard_id, unit_id)
    children = db.scalar(
        select(func.count()).select_from(OrgUnit).where(OrgUnit.parent_id == row.id)
    )
    positions = db.scalar(
        select(func.count()).select_from(OrgPosition).where(OrgPosition.org_unit_id == row.id)
    )
    if children or positions:
        raise AppError(
            "ORG_UNIT_NOT_EMPTY",
            "该组织下仍有子单元或岗位，请先移动或删除，或改为归档。",
            http_status=409,
            details={"children": int(children or 0), "positions": int(positions or 0)},
        )
    db.delete(row)
    db.flush()


def create_position(
    db: Session,
    *,
    yard_id: str,
    actor_id: str,
    org_unit_id: str,
    title: str,
    responsibilities: str | None,
    headcount: int,
    agent_id: str | None,
    sort_order: int,
) -> OrgPosition:
    _get_unit(db, yard_id, org_unit_id)
    _ensure_agent(db, yard_id, agent_id)
    row = OrgPosition(
        yard_id=yard_id,
        org_unit_id=org_unit_id,
        title=_clean_name(title),
        responsibilities=(responsibilities or "").strip() or None,
        headcount=headcount,
        agent_id=agent_id,
        sort_order=sort_order,
        status=UNIT_STATUS_ACTIVE,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(row)
    db.flush()
    return row


def update_position(
    db: Session,
    *,
    yard_id: str,
    actor_id: str,
    position_id: str,
    changes: dict[str, Any],
) -> OrgPosition:
    row = _get_position(db, yard_id, position_id)
    if "org_unit_id" in changes and changes["org_unit_id"] is not None:
        _get_unit(db, yard_id, str(changes["org_unit_id"]))
        row.org_unit_id = str(changes["org_unit_id"])
    if "title" in changes and changes["title"] is not None:
        row.title = _clean_name(str(changes["title"]))
    if "responsibilities" in changes:
        row.responsibilities = (str(changes["responsibilities"] or "")).strip() or None
    if "headcount" in changes and changes["headcount"] is not None:
        row.headcount = int(changes["headcount"])
    if "agent_id" in changes:
        agent_id = changes["agent_id"]
        _ensure_agent(db, yard_id, agent_id)
        row.agent_id = agent_id
    if "sort_order" in changes and changes["sort_order"] is not None:
        row.sort_order = int(changes["sort_order"])
    if "status" in changes and changes["status"] is not None:
        status = str(changes["status"])
        if status not in UNIT_STATUSES:
            raise AppError("ORG_INVALID", f"不支持的状态：{status}", http_status=400)
        row.status = status
    row.updated_by = actor_id
    db.flush()
    return row


def delete_position(db: Session, *, yard_id: str, position_id: str) -> None:
    row = _get_position(db, yard_id, position_id)
    db.delete(row)
    db.flush()


def unit_detail(db: Session, yard_id: str, unit_id: str) -> dict[str, Any]:
    row = _get_unit(db, yard_id, unit_id)
    node = _unit_out(row)
    positions = list(
        db.execute(
            select(OrgPosition)
            .where(OrgPosition.yard_id == yard_id, OrgPosition.org_unit_id == row.id)
            .order_by(OrgPosition.sort_order, OrgPosition.title)
        )
        .scalars()
        .all()
    )
    agents = _agents_map(db, yard_id, {p.agent_id for p in positions if p.agent_id})
    node["positions"] = [_position_out(p, agents) for p in positions]
    return node


def position_detail(db: Session, yard_id: str, position_id: str) -> dict[str, Any]:
    row = _get_position(db, yard_id, position_id)
    agents = _agents_map(db, yard_id, {row.agent_id} if row.agent_id else set())
    return _position_out(row, agents)
