"""租户配额计量（第五期 5.2）：用量统计、超限拦截、状态查询。

模型 TenantQuota 与业务逻辑同文件，方便 ensure/check/get 三函数自包含；
signup worker 只需调用 ensure_quota(db, tenant_id)（幂等）即完成配额初始化。
"""

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.errors import AppError
from app.db.base import Base, TimestampMixin

_QUOTA_FIELDS: dict[str, tuple[str, str]] = {
    "llm_calls": ("llm_calls_used", "llm_calls_limit"),
    "knowledge_docs": ("knowledge_docs_used", "knowledge_docs_limit"),
    "tasks": ("tasks_used", "tasks_limit"),
}

DEFAULT_LIMITS: dict[str, int] = {
    "llm_calls": 1000,
    "knowledge_docs": 100,
    "tasks": 500,
}

WARNING_THRESHOLD = 90


class TenantQuota(Base, TimestampMixin):
    __tablename__ = "tenant_quotas"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_tenant_quotas_tenant"),)

    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    llm_calls_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)
    llm_calls_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    knowledge_docs_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    knowledge_docs_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tasks_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    tasks_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


def ensure_quota(db: Session, tenant_id: str) -> TenantQuota:
    quota = db.query(TenantQuota).filter(TenantQuota.tenant_id == tenant_id).first()
    if quota is not None:
        return quota
    quota = TenantQuota(tenant_id=tenant_id)
    db.add(quota)
    db.flush()
    return quota


def check_and_consume(db: Session, tenant_id: str, kind: str, amount: int = 1) -> None:
    fields = _QUOTA_FIELDS.get(kind)
    if fields is None:
        raise AppError("INVALID_REQUEST", f"未知配额类型：{kind}。", http_status=400)
    if amount < 1:
        raise AppError("INVALID_REQUEST", "配额扣减数量必须为正整数。", http_status=400)
    quota = ensure_quota(db, tenant_id)
    used_attr, limit_attr = fields
    used = getattr(quota, used_attr)
    limit = getattr(quota, limit_attr)
    if used + amount > limit:
        raise AppError(
            "QUOTA_EXCEEDED",
            f"{kind} 配额已用尽（已用 {used}/{limit}）。",
            http_status=429,
            details={"kind": kind, "used": used, "limit": limit},
        )
    setattr(quota, used_attr, used + amount)
    db.commit()


def get_quota_status(db: Session, tenant_id: str) -> dict:
    quota = ensure_quota(db, tenant_id)
    items: dict[str, dict] = {}
    warnings: list[dict] = []
    for kind, (used_attr, limit_attr) in _QUOTA_FIELDS.items():
        used = int(getattr(quota, used_attr))
        limit = int(getattr(quota, limit_attr))
        percent = round(used / limit * 100, 2) if limit > 0 else 0.0
        items[kind] = {
            "limit": limit,
            "used": used,
            "remaining": max(limit - used, 0),
            "usage_percent": percent,
        }
        if percent >= WARNING_THRESHOLD:
            warnings.append(
                {
                    "kind": kind,
                    "level": "warning",
                    "message": f"{kind} 配额使用率已达 {percent}%（{used}/{limit}）。",
                }
            )
    return {"tenant_id": tenant_id, "quotas": items, "warnings": warnings}
