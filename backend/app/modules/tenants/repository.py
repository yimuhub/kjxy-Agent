"""租户模块数据访问层。"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.auth.models import User
from app.modules.tenants.models import Tenant, TenantMembership


def get_tenant(db: Session, tenant_id: str) -> Tenant | None:
    return db.get(Tenant, tenant_id)


def create_tenant(
    db: Session,
    *,
    name: str,
    province: str | None = None,
    city: str | None = None,
    scene_type: str | None = None,
    config: dict | None = None,
) -> Tenant:
    tenant = Tenant(
        name=name,
        status="active",
        province=province,
        city=city,
        scene_type=scene_type,
        config_json=config,
    )
    db.add(tenant)
    db.flush()
    return tenant


def get_memberships_by_user(db: Session, user_id: str) -> list[TenantMembership]:
    return list(
        db.execute(
            select(TenantMembership)
            .where(TenantMembership.user_id == user_id, TenantMembership.status == "active")
            .order_by(TenantMembership.created_at)
        )
        .scalars()
        .all()
    )


def get_membership(db: Session, tenant_id: str, user_id: str) -> TenantMembership | None:
    return db.execute(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user_id,
            TenantMembership.status == "active",
        )
    ).scalar_one_or_none()


def add_membership(db: Session, *, tenant_id: str, user_id: str, role: str) -> TenantMembership:
    membership = TenantMembership(tenant_id=tenant_id, user_id=user_id, role=role, status="active")
    db.add(membership)
    db.flush()
    return membership


def remove_membership(db: Session, membership: TenantMembership) -> None:
    db.delete(membership)
    db.flush()


def count_active_admins(db: Session, tenant_id: str) -> int:
    return db.execute(
        select(func.count())
        .select_from(TenantMembership)
        .where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.role == "tenant_admin",
            TenantMembership.status == "active",
        )
    ).scalar_one()


def list_members(
    db: Session, tenant_id: str, *, page: int, page_size: int
) -> tuple[list[tuple[TenantMembership, User]], int]:
    """返回 (membership, user) 元组列表与总数。"""
    total = db.execute(
        select(func.count())
        .select_from(TenantMembership)
        .where(TenantMembership.tenant_id == tenant_id)
    ).scalar_one()
    rows = db.execute(
        select(TenantMembership, User)
        .join(User, User.id == TenantMembership.user_id)
        .where(TenantMembership.tenant_id == tenant_id)
        .order_by(TenantMembership.created_at)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return [(m, u) for m, u in rows], total
