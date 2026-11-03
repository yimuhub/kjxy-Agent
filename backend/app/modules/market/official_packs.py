"""官方技能包服务（v3.56）：按小院画像推荐 + 幂等同步启用。

与 builtin 12 技能的区别：official packs 带 yard_types 标签，按小院设置
``yard_profile.types`` 针对性推荐；sync 走 ``ensure_skills``（published 直通、
按 yard+code 幂等跳过），重复同步无副作用——官方更新 = 新 release 带新目录
版本，负责人在「官方技能库」一键同步。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.modules.audit.service import log_audit
from app.modules.market.builtin_seed import ensure_skills
from app.modules.market.official_catalog import (
    CATALOG_VERSION,
    OFFICIAL_PACKS,
    PACK_MAP,
    YARD_TYPES,
    pack_skill_items,
    packs_for_yard_types,
)

PACK_SYNC_ACTIONS = {
    "common": "official_pack_sync_common",
}


def _installed_codes(db: Session, yard_id: str) -> set[str]:
    from app.modules.evolution.models import Skill

    return {
        str(code)
        for code in db.scalars(select(Skill.skill_code).where(Skill.yard_id == yard_id)).all()
    }


def list_official_packs(
    db: Session, yard_id: str, types: list[str] | None = None
) -> dict[str, Any]:
    """目录视图：每包的技能清单 + 本院已启用数 + 是否为画像推荐。"""
    installed = _installed_codes(db, yard_id)
    recommended = {p["key"] for p in packs_for_yard_types(types or [])}
    packs: list[dict[str, Any]] = []
    for pack in OFFICIAL_PACKS:
        skills = [
            {
                "code": spec["code"],
                "name": spec["name"],
                "description": spec["description"],
                "installed": spec["code"] in installed,
            }
            for spec in pack["skills"]
        ]
        packs.append(
            {
                "key": pack["key"],
                "name": pack["name"],
                "note": pack["note"],
                "yard_types": list(pack["yard_types"]),
                "recommended": pack["key"] in recommended,
                "skill_count": len(skills),
                "installed_count": sum(1 for s in skills if s["installed"]),
                "skills": skills,
            }
        )
    return {"catalog_version": CATALOG_VERSION, "yard_types": list(YARD_TYPES), "packs": packs}


def sync_official_pack(db: Session, ctx: YardContext, pack_key: str) -> dict[str, Any]:
    """按包同步启用：published 直通、幂等跳过已存在。返回（新增数, 跳过数）。"""
    pack = PACK_MAP.get(pack_key)
    if pack is None:
        from app.core.errors import AppError

        raise AppError(
            "OFFICIAL_PACK_NOT_FOUND",
            "官方技能包不存在。",
            http_status=404,
            details={"pack": pack_key},
        )
    before = _installed_codes(db, ctx.yard_id)
    items = pack_skill_items(pack_key)
    created = ensure_skills(
        db,
        items,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        request_id=ctx.request_id,
        source_label=f"official:{pack_key}",
    )
    skipped = sum(1 for item in items if str(item["code"]) in before)
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="official_pack_synced",
        resource_type="market_pack",
        resource_id=pack_key,
        result="success",
        request_id=ctx.request_id,
        metadata={
            "pack": pack_key,
            "created": created,
            "skipped": skipped,
            "catalog_version": CATALOG_VERSION,
        },
    )
    return {
        "pack": pack_key,
        "created": created,
        "skipped": skipped,
        "catalog_version": CATALOG_VERSION,
    }


__all__ = ["list_official_packs", "sync_official_pack"]
