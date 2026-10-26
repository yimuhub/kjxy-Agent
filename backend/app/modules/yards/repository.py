"""小院模块数据访问层。"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.auth.models import User
from app.modules.yards.models import Yard, YardMembership


def get_yard(db: Session, yard_id: str) -> Yard | None:
    return db.get(Yard, yard_id)


def create_yard(
    db: Session,
    *,
    name: str,
    province: str | None = None,
    city: str | None = None,
    scene_type: str | None = None,
    config: dict | None = None,
) -> Yard:
    yard = Yard(
        name=name,
        status="active",
        province=province,
        city=city,
        scene_type=scene_type,
        config_json=config,
    )
    db.add(yard)
    db.flush()
    return yard


def get_memberships_by_user(db: Session, user_id: str) -> list[YardMembership]:
    return list(
        db.execute(
            select(YardMembership)
            .where(YardMembership.user_id == user_id, YardMembership.status == "active")
            .order_by(YardMembership.created_at)
        )
        .scalars()
        .all()
    )


def get_membership(db: Session, yard_id: str, user_id: str) -> YardMembership | None:
    return db.execute(
        select(YardMembership).where(
            YardMembership.yard_id == yard_id,
            YardMembership.user_id == user_id,
            YardMembership.status == "active",
        )
    ).scalar_one_or_none()


def add_membership(db: Session, *, yard_id: str, user_id: str, role: str) -> YardMembership:
    membership = YardMembership(yard_id=yard_id, user_id=user_id, role=role, status="active")
    db.add(membership)
    db.flush()
    return membership


def remove_membership(db: Session, membership: YardMembership) -> None:
    db.delete(membership)
    db.flush()


def count_active_admins(db: Session, yard_id: str) -> int:
    return db.execute(
        select(func.count())
        .select_from(YardMembership)
        .where(
            YardMembership.yard_id == yard_id,
            YardMembership.role == "yard_admin",
            YardMembership.status == "active",
        )
    ).scalar_one()


def list_members(
    db: Session, yard_id: str, *, page: int, page_size: int
) -> tuple[list[tuple[YardMembership, User]], int]:
    """返回 (membership, user) 元组列表与总数。"""
    total = db.execute(
        select(func.count()).select_from(YardMembership).where(YardMembership.yard_id == yard_id)
    ).scalar_one()
    rows = db.execute(
        select(YardMembership, User)
        .join(User, User.id == YardMembership.user_id)
        .where(YardMembership.yard_id == yard_id)
        .order_by(YardMembership.created_at)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return [(m, u) for m, u in rows], total
