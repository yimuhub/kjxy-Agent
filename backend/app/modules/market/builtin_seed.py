"""内置技能种子的通用落库逻辑（农业、增长/服务域、办公域共用）。

三个产出方：
- ``market/agri_skills.py``：12 项农技技能；
- ``growth/skills.py``：营销 6 项 + 活动 4 项；
- ``market/office_skills.py``：Word/Excel/PPT 等 6 项办公技能。

三者条目结构一致（``MarketItem`` 字段 + ``definition_json``），因此落库与
「新小院铺技能」只保留一份实现，避免多处漂移。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.service import log_audit
from app.modules.evolution.models import Skill
from app.modules.market.models import MarketItem


def seed_items(db: Session, items: list[dict[str, Any]]) -> int:
    """幂等 upsert 内置条目；返回新增条数（已存在的只刷新内容，不改状态）。"""
    created = 0
    for data in items:
        row = db.query(MarketItem).filter(MarketItem.item_key == data["item_key"]).first()
        if row is None:
            db.add(MarketItem(**data))
            created += 1
            continue
        # 只刷新内容字段；status 保留安装态，避免种子覆盖用户/安装结果
        row.name = data["name"]
        row.version = data["version"]
        row.description = data["description"]
        row.source_url = data["source_url"]
        row.license = data["license"]
        row.author = data["author"]
        row.risk_level = data["risk_level"]
        row.definition_json = data["definition_json"]
        row.metadata_json = data["metadata_json"]
        row.verified = True
        row.verified_at = row.verified_at or datetime.now(UTC)
    db.flush()
    return created


def ensure_skills(
    db: Session,
    items: list[dict[str, Any]],
    *,
    yard_id: str,
    actor_id: str,
    request_id: str = "",
    source_label: str = "",
) -> int:
    """为新小院铺设内置技能（published），返回新建数量。

    幂等：按 yard + skill_code 判重，已存在（任意版本）则跳过。
    """
    existing = {
        str(code)
        for code in db.scalars(select(Skill.skill_code).where(Skill.yard_id == yard_id)).all()
    }
    created = 0
    for item in items:
        code = str(item["code"])
        if code in existing:
            continue
        definition = dict(item["definition_json"] or {})
        definition["market_item_key"] = item["item_key"]
        db.add(
            Skill(
                yard_id=yard_id,
                skill_code=code,
                version=str(item["version"]),
                definition_json=definition,
                status="published",
                created_from="market_builtin",
                approved_by=actor_id,
            )
        )
        existing.add(code)
        created += 1
    if created:
        db.flush()
        log_audit(
            db,
            yard_id=yard_id,
            actor_id=actor_id,
            action="builtin_skills_provisioned",
            resource_type="skill",
            resource_id=yard_id,
            result="success",
            request_id=request_id or f"provision:{yard_id}",
            metadata={"count": created, "source": source_label},
        )
    return created


__all__ = ["ensure_skills", "seed_items"]
