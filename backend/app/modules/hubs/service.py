"""枢纽服务（v3.65）：创建/绑定/成员/模式化聚合（监管看板的收窄版）。

铁律与监管看板一致：聚合**只出数字与时间，不含日志内容**；聚合范围按
``yards.hub_id`` 收窄——枢纽 manager 只看所辖小院。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.audit.service import log_audit
from app.modules.hubs.models import (
    MODE_FOCUS,
    MODE_LABELS,
    ROLE_MANAGER,
    VALID_HUB_ROLES,
    VALID_MODES,
    HubMember,
    YardHub,
)
from app.modules.journal.models import YardJournal
from app.modules.yards.models import Yard

STALE_DAYS = 7


# ---------------------------------------------------------------------------
# 身份与判权
# ---------------------------------------------------------------------------


def is_hub_manager(db: Session, user_id: str, hub_id: str) -> bool:
    row = db.scalar(
        select(HubMember).where(
            HubMember.hub_id == hub_id,
            HubMember.user_id == user_id,
            HubMember.role == ROLE_MANAGER,
        )
    )
    return row is not None


def list_managed_hubs(db: Session, user_id: str) -> list[YardHub]:
    """用户可管理的枢纽：platform_admin 全部，普通用户按 hub_members。"""

    hubs = list(db.scalars(select(YardHub).order_by(YardHub.created_at.desc())).all())
    return [h for h in hubs if is_hub_manager(db, user_id, h.id)]


def require_hub_access(
    db: Session, ctx: YardContext, hub_id: str, *, manager: bool = False
) -> YardHub:
    """取枢纽并校验访问权：manager 要求 role=manager（platform_admin 直通）。"""
    hub = db.get(YardHub, hub_id)
    if hub is None:
        raise AppError("HUB_NOT_FOUND", "枢纽不存在或无权访问。", http_status=404)
    if "platform_admin" in (ctx.roles or []):
        return hub
    ok = is_hub_manager(db, ctx.user_id, hub_id) if manager else _is_member(db, ctx.user_id, hub_id)
    if not ok:
        raise AppError("FORBIDDEN", "你不是该枢纽的成员。", http_status=403)
    return hub


def _is_member(db: Session, user_id: str, hub_id: str) -> bool:
    return (
        db.scalar(
            select(HubMember).where(HubMember.hub_id == hub_id, HubMember.user_id == user_id)
        )
        is not None
    )


# ---------------------------------------------------------------------------
# CRUD 与绑定
# ---------------------------------------------------------------------------


def create_hub(
    db: Session, ctx: YardContext, *, name: str, mode: str, region: str, note: str
) -> YardHub:
    if mode not in VALID_MODES:
        raise AppError(
            "INVALID_REQUEST", f"mode 只能是 {VALID_MODES}。", http_status=400
        )
    exists = db.scalar(select(YardHub).where(YardHub.name == name))
    if exists is not None:
        raise AppError("HUB_NAME_TAKEN", "同名枢纽已存在。", http_status=409)
    hub = YardHub(
        id=uuid4().hex,
        name=name[:120],
        mode=mode,
        region=region[:120],
        note=note[:500],
        created_by=ctx.user_id,
    )
    db.add(hub)
    db.flush()
    # 创建者自动成为 manager
    db.add(HubMember(hub_id=hub.id, user_id=ctx.user_id, role=ROLE_MANAGER))
    db.flush()
    log_audit(
        db,
        yard_id=None,
        actor_id=ctx.user_id,
        action="hub_created",
        resource_type="hub",
        resource_id=hub.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"name": hub.name, "mode": mode, "region": hub.region},
    )
    return hub


def attach_yard(db: Session, ctx: YardContext, hub_id: str, yard_id: str) -> dict[str, Any]:
    """把小院绑到枢纽（一个小院只属一个枢纽；重复绑定幂等返回）。"""
    hub = require_hub_access(db, ctx, hub_id, manager=True)
    yard = db.get(Yard, yard_id)
    if yard is None:
        raise AppError("YARD_NOT_FOUND", "小院不存在。", http_status=404)
    old_hub_id = yard.hub_id
    if old_hub_id == hub.id:
        return {"yard_id": yard_id, "hub_id": hub.id, "moved": False}
    if old_hub_id:
        old = db.get(YardHub, old_hub_id)
        raise AppError(
            "YARD_ALREADY_ATTACHED",
            f"该小院已归属枢纽「{old.name if old else old_hub_id}」，请先解绑。",
            http_status=409,
        )
    yard.hub_id = hub.id
    db.flush()
    log_audit(
        db,
        yard_id=None,
        actor_id=ctx.user_id,
        action="hub_yard_attached",
        resource_type="hub",
        resource_id=hub.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"yard_id": yard_id, "yard_name": yard.name},
    )
    return {"yard_id": yard_id, "yard_name": yard.name, "hub_id": hub.id, "moved": True}


def detach_yard(db: Session, ctx: YardContext, yard_id: str) -> dict[str, Any]:
    yard = db.get(Yard, yard_id)
    if yard is None or not yard.hub_id:
        raise AppError("YARD_NOT_ATTACHED", "该小院未归属任何枢纽。", http_status=404)
    require_hub_access(db, ctx, yard.hub_id, manager=True)
    hub_id = yard.hub_id
    yard.hub_id = None
    db.flush()
    log_audit(
        db,
        yard_id=None,
        actor_id=ctx.user_id,
        action="hub_yard_detached",
        resource_type="hub",
        resource_id=hub_id,
        result="success",
        request_id=ctx.request_id,
        metadata={"yard_id": yard_id},
    )
    return {"yard_id": yard_id, "hub_id": None}


def add_member(
    db: Session, ctx: YardContext, hub_id: str, user_id: str, role: str
) -> dict[str, Any]:
    require_hub_access(db, ctx, hub_id, manager=True)
    if role not in VALID_HUB_ROLES:
        raise AppError("INVALID_REQUEST", f"role 只能是 {VALID_HUB_ROLES}。", http_status=400)
    exists = db.scalar(
        select(HubMember).where(HubMember.hub_id == hub_id, HubMember.user_id == user_id)
    )
    if exists is not None:
        raise AppError("HUB_MEMBER_EXISTS", "该成员已在枢纽中。", http_status=409)
    db.add(HubMember(hub_id=hub_id, user_id=user_id, role=role))
    db.flush()
    log_audit(
        db,
        yard_id=None,
        actor_id=ctx.user_id,
        action="hub_member_added",
        resource_type="hub",
        resource_id=hub_id,
        result="success",
        request_id=ctx.request_id,
        metadata={"member": user_id, "role": role},
    )
    return {"hub_id": hub_id, "user_id": user_id, "role": role}


# ---------------------------------------------------------------------------
# 模式化聚合（监管看板收窄版）
# ---------------------------------------------------------------------------


def hub_out(db: Session, hub: YardHub) -> dict[str, Any]:
    yard_count = int(
        db.scalar(select(func.count()).select_from(Yard).where(Yard.hub_id == hub.id)) or 0
    )
    members = list(
        db.scalars(select(HubMember).where(HubMember.hub_id == hub.id)).all()
    )
    return {
        "id": hub.id,
        "name": hub.name,
        "mode": hub.mode,
        "mode_label": MODE_LABELS.get(hub.mode, hub.mode),
        "mode_focus": MODE_FOCUS.get(hub.mode, ""),
        "region": hub.region,
        "note": hub.note,
        "yard_count": yard_count,
        "members": [{"user_id": m.user_id, "role": m.role} for m in members],
        "created_by": hub.created_by,
    }


def hub_overview(db: Session, ctx: YardContext, hub_id: str) -> dict[str, Any]:
    """枢纽工作台聚合：所辖小院的日志量/活跃作者/断更预警（按 mode 侧重由前端呈现）。"""
    hub = require_hub_access(db, ctx, hub_id)
    yards = list(db.scalars(select(Yard).where(Yard.hub_id == hub.id)).all())
    yard_ids = [y.id for y in yards]
    names = {y.id: y.name for y in yards}
    month_prefix = datetime.now(UTC).strftime("%Y-%m") + "%"
    stale_before = datetime.now(UTC) - timedelta(days=STALE_DAYS)

    items: list[dict[str, Any]] = []
    for yid in yard_ids:
        total, authors, last_at = (
            db.execute(
                select(
                    func.count(),
                    func.count(func.distinct(YardJournal.author_user_id)),
                    func.max(YardJournal.updated_at),
                ).where(
                    YardJournal.yard_id == yid,
                    YardJournal.period_key.like(month_prefix),
                )
            )
        ).one()
        last_at = last_at if last_at is None or last_at.tzinfo else last_at.replace(tzinfo=UTC)
        items.append(
            {
                "yard_id": yid,
                "yard_name": names.get(yid, yid[:8]),
                "journal_count": int(total),
                "active_authors": int(authors),
                "last_updated_at": last_at.isoformat() if last_at else None,
                "stale": last_at is None or last_at < stale_before,
            }
        )
    items.sort(key=lambda i: -i["journal_count"])
    stale_count = sum(1 for i in items if i["stale"])
    return {
        "hub": hub_out(db, hub),
        "stale_days": STALE_DAYS,
        "stale_count": stale_count,
        "items": items,
    }
