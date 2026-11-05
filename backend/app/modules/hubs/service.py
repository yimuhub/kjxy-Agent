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
    MODE_MUNICIPAL,
    MODE_PROVINCE,
    MODE_STATION,
    ROLE_MANAGER,
    VALID_HUB_ROLES,
    VALID_MODES,
    HubMember,
    YardHub,
)
from app.modules.journal.models import YardJournal
from app.modules.yards.models import Yard

STALE_DAYS = 7

#: 制度锚点：每院每年不少于 100 篇日志 → 月均 9 篇作为达标线（100/12 向上取整）
ANNUAL_JOURNAL_ANCHOR = 100
MONTHLY_JOURNAL_ANCHOR = 9

#: 枢纽权限包：view 给只读可见者；manage/export/dispatch 只给 manager。
#: 不放 JWT——每次从 hub_members 实时判定，解绑/降角色立即生效。
HUB_VIEW_PERMISSIONS: frozenset[str] = frozenset({"hubs:view"})
HUB_MANAGER_PERMISSIONS: frozenset[str] = frozenset(
    {"hubs:view", "hubs:manage", "hubs:export", "hubs:dispatch"}
)

#: 各模式的分组维度：省级按培养单位、市级按县域、实验站按小院类型
MODE_GROUP_FIELD: dict[str, tuple[str, str]] = {
    MODE_PROVINCE: ("unit", "培养单位"),
    MODE_MUNICIPAL: ("county", "县域"),
    MODE_STATION: ("yard_type", "小院类型"),
}


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


def list_visible_hubs(
    db: Session, user_id: str, *, is_platform_admin: bool = False
) -> list[tuple[YardHub, str]]:
    """用户可见的枢纽列表，返回 (枢纽, 我的角色)。

    角色：manager（hub_members 且 role=manager）、viewer（hub_members）、
    yard_manager（所辖小院的负责人/管理员——v3.65 修复可见性）。
    """
    hubs = list(db.scalars(select(YardHub).order_by(YardHub.created_at.desc())).all())
    out: list[tuple[YardHub, str]] = []
    for h in hubs:
        if is_platform_admin:
            out.append((h, "manager"))
            continue
        if is_hub_manager(db, user_id, h.id):
            out.append((h, "manager"))
        elif _is_member(db, user_id, h.id):
            out.append((h, "viewer"))
        elif is_yard_manager_of_hub(db, user_id, h.id):
            out.append((h, "yard_manager"))
    return out


def list_managed_hubs(db: Session, user_id: str) -> list[YardHub]:
    """兼容别名：仅 manager 角色。"""
    return [h for h, role in list_visible_hubs(db, user_id) if role == "manager"]


def hub_permissions(
    db: Session, user_id: str, *, is_platform_admin: bool = False
) -> frozenset[str]:
    """枢纽作用域权限包（v3.66）。

    与 JWT 角色解耦：纯枢纽账号（不挂任何小院）也能拿到 hubs:*；
    小院管理员不会因此获得 export/dispatch——那两个动作只给枢纽 manager。
    """
    if is_platform_admin:
        return HUB_MANAGER_PERMISSIONS
    roles = {role for _, role in list_visible_hubs(db, user_id)}
    if ROLE_MANAGER in roles:
        return HUB_MANAGER_PERMISSIONS
    if roles:
        return HUB_VIEW_PERMISSIONS
    return frozenset()


def hub_context(
    db: Session, user_id: str, *, is_platform_admin: bool = False
) -> list[dict[str, Any]]:
    """登录后前端需要的枢纽上下文（只带归属与角色，不含聚合数字）。"""
    return [
        {
            "hub_id": hub.id,
            "name": hub.name,
            "mode": hub.mode,
            "mode_label": MODE_LABELS.get(hub.mode, hub.mode),
            "mode_focus": MODE_FOCUS.get(hub.mode, ""),
            "region": hub.region,
            "role": role,
        }
        for hub, role in list_visible_hubs(db, user_id, is_platform_admin=is_platform_admin)
    ]


def is_yard_manager_of_hub(db: Session, user_id: str, hub_id: str) -> bool:
    """用户是否为该枢纽**所辖小院**的负责人/管理员（活跃成员）。

    这类用户对枢纽聚合有**只读**可见权——把演示科技小院绑进枢纽后，该院
    管理员理应看到自己院所在枢纽的工作台（v3.65 修复：此前仅 hub_members 可见）。
    """
    from app.modules.yards.models import YardMembership

    return (
        db.scalar(
            select(YardMembership.user_id)
            .join(Yard, Yard.id == YardMembership.yard_id)
            .where(
                Yard.hub_id == hub_id,
                YardMembership.user_id == user_id,
                YardMembership.status == "active",
                YardMembership.role.in_(["yard_owner", "yard_admin"]),
            )
            .limit(1)
        )
        is not None
    )


def require_hub_access(
    db: Session, ctx: YardContext, hub_id: str, *, manager: bool = False
) -> YardHub:
    """取枢纽并校验访问权：manager 要求 role=manager（platform_admin 直通）。"""
    hub = db.get(YardHub, hub_id)
    if hub is None:
        raise AppError("HUB_NOT_FOUND", "枢纽不存在或无权访问。", http_status=404)
    if "platform_admin" in (ctx.roles or []):
        return hub
    if manager:
        if not is_hub_manager(db, ctx.user_id, hub_id):
            raise AppError("FORBIDDEN", "只有枢纽管理员可执行该操作。", http_status=403)
        return hub
    # 只读可见：枢纽成员 或 所辖小院的负责人/管理员
    if _is_member(db, ctx.user_id, hub_id) or is_yard_manager_of_hub(db, ctx.user_id, hub_id):
        return hub
    raise AppError("FORBIDDEN", "你不是该枢纽的成员。", http_status=403)


def _is_member(db: Session, user_id: str, hub_id: str) -> bool:
    return (
        db.scalar(select(HubMember).where(HubMember.hub_id == hub_id, HubMember.user_id == user_id))
        is not None
    )


# ---------------------------------------------------------------------------
# CRUD 与绑定
# ---------------------------------------------------------------------------


def create_hub(
    db: Session, ctx: YardContext, *, name: str, mode: str, region: str, note: str
) -> YardHub:
    if mode not in VALID_MODES:
        raise AppError("INVALID_REQUEST", f"mode 只能是 {VALID_MODES}。", http_status=400)
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


def list_attachable_yards(db: Session, user: Any) -> list[dict[str, Any]]:
    """当前用户可绑定到枢纽的小院清单（下拉用）。

    口径：platform_admin → 全部小院；普通用户 → 其担任 yard_owner/yard_admin
    的活跃成员小院。返回带「当前归属枢纽」标记，前端据此禁用已绑定项。
    """

    from app.modules.yards.models import YardMembership

    if "platform_admin" in (user.roles or []):
        yards = list(db.scalars(select(Yard).order_by(Yard.name)).all())
    else:
        yards = list(
            db.scalars(
                select(Yard)
                .join(YardMembership, YardMembership.yard_id == Yard.id)
                .where(
                    YardMembership.user_id == user.user_id,
                    YardMembership.status == "active",
                    YardMembership.role.in_(["yard_owner", "yard_admin"]),
                )
                .order_by(Yard.name)
            ).all()
        )
    out: list[dict[str, Any]] = []
    for y in yards:
        hub_name = None
        if y.hub_id:
            hub = db.get(YardHub, y.hub_id)
            hub_name = hub.name if hub else None
        out.append(
            {
                "id": y.id,
                "name": y.name,
                "hub_id": y.hub_id,
                "hub_name": hub_name,
            }
        )
    return out


def member_candidates(db: Session, ctx: YardContext, hub_id: str) -> list[dict[str, Any]]:
    """可添加为枢纽成员的用户清单（下拉用）。

    口径：活跃注册用户，排除已在本枢纽的；manager 调用（router 已判权）。
    """
    from app.modules.auth.models import User

    existing = {
        str(uid)
        for uid in db.scalars(select(HubMember.user_id).where(HubMember.hub_id == hub_id)).all()
    }
    users = list(
        db.scalars(select(User).where(User.status == "active").order_by(User.username)).all()
    )
    return [
        {
            "id": u.id,
            "username": u.username,
            "display_name": u.display_name or u.username,
            "already_member": u.id in existing,
        }
        for u in users
        if u.id not in existing
    ]


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
    members = list(db.scalars(select(HubMember).where(HubMember.hub_id == hub.id)).all())
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


def _yard_attr(yard: Yard, key: str, fallback: str = "") -> str:
    """读小院特性：``attributes_json`` 优先，回退到模型列（province/city/scene_type）。"""
    raw = getattr(yard, "attributes_json", None)
    if isinstance(raw, dict):
        value = str(raw.get(key) or "").strip()
        if value:
            return value
    value = str(getattr(yard, key, "") or "").strip()
    return value or fallback


def _group_key(yard: Yard, mode: str) -> str:
    if mode == MODE_MUNICIPAL:
        return _yard_attr(yard, "county") or str(yard.city or "").strip() or "未标注县域"
    if mode == MODE_STATION:
        return _yard_attr(yard, "yard_type") or str(yard.scene_type or "").strip() or "未标注类型"
    return _yard_attr(yard, "unit", "未标注培养单位")


def _mode_insights(
    mode: str,
    items: list[dict[str, Any]],
    yards_by_id: dict[str, Yard],
    *,
    stale_count: int,
) -> dict[str, Any]:
    """按运营模式给出分组视角；结构统一，前端只换标题与列。

    - province：培养单位分组 + 100 篇年锚点的月度达标率
    - municipal：县域分组 + 覆盖率
    - station：小院类型分组 + 横向对比（明细在 items）
    """
    group_by, group_label = MODE_GROUP_FIELD.get(mode, ("unit", "分组"))
    groups: dict[str, dict[str, Any]] = {}
    for item in items:
        yard = yards_by_id.get(item["yard_id"])
        if yard is None:
            continue
        key = _group_key(yard, mode)
        row = groups.setdefault(key, {"key": key, "yards": 0, "journal_count": 0, "compliant": 0})
        row["yards"] += 1
        row["journal_count"] += int(item["journal_count"])
        if int(item["journal_count"]) >= MONTHLY_JOURNAL_ANCHOR:
            row["compliant"] += 1

    ordered = sorted(groups.values(), key=lambda g: (-g["journal_count"], g["key"]))
    total = len(items)
    compliant = sum(1 for i in items if int(i["journal_count"]) >= MONTHLY_JOURNAL_ANCHOR)
    return {
        "mode": mode,
        "group_by": group_by,
        "group_label": group_label,
        "anchor_annual": ANNUAL_JOURNAL_ANCHOR,
        "anchor_monthly": MONTHLY_JOURNAL_ANCHOR,
        "groups": ordered,
        "summary": {
            "yards": total,
            "groups": len(ordered),
            "journal_count": sum(int(i["journal_count"]) for i in items),
            "compliant": compliant,
            "compliance_rate": round(compliant / total, 4) if total else 0.0,
            "stale_count": stale_count,
        },
    }


def hub_overview(db: Session, ctx: YardContext, hub_id: str) -> dict[str, Any]:
    """枢纽工作台聚合：所辖小院的日志量/活跃作者/断更预警 + 按模式分组。"""
    hub = require_hub_access(db, ctx, hub_id)
    yards = list(db.scalars(select(Yard).where(Yard.hub_id == hub.id)).all())
    yards_by_id = {y.id: y for y in yards}
    month_prefix = datetime.now(UTC).strftime("%Y-%m") + "%"
    stale_before = datetime.now(UTC) - timedelta(days=STALE_DAYS)

    items: list[dict[str, Any]] = []
    for yid in yards_by_id:
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
        yard = yards_by_id[yid]
        attributes = yard.attributes_json if isinstance(yard.attributes_json, dict) else {}
        items.append(
            {
                "yard_id": yid,
                "yard_name": yard.name,
                "journal_count": int(total),
                "active_authors": int(authors),
                "last_updated_at": last_at.isoformat() if last_at else None,
                "stale": last_at is None or last_at < stale_before,
                # v3.66：特性字段——三种模式的分组与横向对比都靠它
                "province": yard.province,
                "city": yard.city,
                "scene_type": yard.scene_type,
                "attributes": attributes,
                "group_key": _group_key(yard, hub.mode),
            }
        )
    items.sort(key=lambda i: -i["journal_count"])
    stale_count = sum(1 for i in items if i["stale"])
    return {
        "hub": hub_out(db, hub),
        "stale_days": STALE_DAYS,
        "stale_count": stale_count,
        "items": items,
        "insights": _mode_insights(hub.mode, items, yards_by_id, stale_count=stale_count),
    }
