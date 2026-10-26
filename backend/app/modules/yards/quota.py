"""小院配额计量（第五期 5.2）：用量统计、超限拦截、状态查询。

模型 YardQuota 与业务逻辑同文件，方便 ensure/check/get 三函数自包含；
signup worker 只需调用 ensure_quota(db, yard_id)（幂等）即完成配额初始化。

月度重置说明（规范 §16.1）：YardQuota 没有“所属计费期”列，加列需要 migration
（本轮禁止改 migration）。月度清零由 beat 任务 quota.monthly_reset
（app/workers/heartbeat_tasks.py）落地：Redis 键判定当月是否已清，每日检查、当月仅执行一次。
GET 响应中的 period 为当前自然月标注；运营侧另有 90%/100% 告警与 PATCH 限额调控。
"""

from datetime import UTC, datetime

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.errors import AppError
from app.core.notify import send_alert
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


class YardQuota(Base, TimestampMixin):
    __tablename__ = "yard_quotas"
    __table_args__ = (UniqueConstraint("yard_id", name="uq_yard_quotas_yard"),)

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False)
    llm_calls_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)
    llm_calls_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    knowledge_docs_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    knowledge_docs_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tasks_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    tasks_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


def ensure_quota(db: Session, yard_id: str) -> YardQuota:
    quota = db.query(YardQuota).filter(YardQuota.yard_id == yard_id).first()
    if quota is not None:
        return quota
    quota = YardQuota(yard_id=yard_id)
    db.add(quota)
    db.flush()
    return quota


def check_and_consume(db: Session, yard_id: str, kind: str, amount: int = 1) -> None:
    fields = _QUOTA_FIELDS.get(kind)
    if fields is None:
        raise AppError("INVALID_REQUEST", f"未知配额类型：{kind}。", http_status=400)
    if amount < 1:
        raise AppError("INVALID_REQUEST", "配额扣减数量必须为正整数。", http_status=400)
    quota = ensure_quota(db, yard_id)
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
    _alert_thresholds(yard_id, kind, used + amount, limit)


def _alert_thresholds(yard_id: str, kind: str, new_used: int, limit: int) -> None:
    """跨越 90%/100% 阈值时发运维告警（规范 §16.2）。

    dedupe_key 含阈值档位：同一进程内同档位只推一次，防止刷屏；告警失败不影响业务。
    """
    if limit <= 0:
        return
    if new_used >= limit:
        send_alert(
            title="quota",
            message=f"小院 {yard_id} 的 {kind} 配额已用尽（{new_used}/{limit}）。",
            level="critical",
            dedupe_key=f"quota:{yard_id}:{kind}:100",
        )
    elif new_used / limit * 100 >= WARNING_THRESHOLD:
        send_alert(
            title="quota",
            message=(
                f"小院 {yard_id} 的 {kind} 使用率已达 "
                f"{WARNING_THRESHOLD}%（{new_used}/{limit}）。"
            ),
            level="warning",
            dedupe_key=f"quota:{yard_id}:{kind}:90",
        )


def current_period() -> str:
    """当前自然月所属期标注（YYYY-MM）。真正的月度清零见模块 docstring。"""
    now = datetime.now(UTC)
    return f"{now.year:04d}-{now.month:02d}"


def get_quota_status(db: Session, yard_id: str) -> dict:
    quota = ensure_quota(db, yard_id)
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
    return {
        "yard_id": yard_id,
        "period": current_period(),
        "quotas": items,
        "warnings": warnings,
    }
