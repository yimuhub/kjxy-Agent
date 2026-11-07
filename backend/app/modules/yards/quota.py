"""小院配额计量（第五期 5.2）：用量统计、超限拦截、状态查询。

模型 YardQuota 与业务逻辑同文件，方便 ensure/check/get 三函数自包含；
signup worker 只需调用 ensure_quota(db, yard_id)（幂等）即完成配额初始化。

月度重置说明（规范 §16.1）：月度清零由 ``quota.monthly_reset``
（app/workers/heartbeat_tasks.py）落地，幂等标记是 ``reset_period`` 列上的条件
UPDATE——服务器由 beat 每日检查，桌面单机由内嵌维护线程检查，两条通道共用同一实现，
不依赖 Redis。GET 响应中的 period 为当前自然月标注；运营侧另有 90%/100% 告警与
PATCH 限额调控。
"""

from datetime import UTC, datetime

from sqlalchemy import Integer, String, UniqueConstraint, select, update
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

#: 额度类型中文名（告警、状态、界面统一口径，避免把 llm_calls 这种码抛给用户）
KIND_LABELS: dict[str, str] = {
    "llm_calls": "模型调用",
    "knowledge_docs": "知识库文档",
    "tasks": "任务",
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
    #: 最近一次自然月清零所属期（YYYY-MM）。作为幂等标记：Quota.monthly_reset 只
    #: 更新「NULL 或不是本月」的行，多进程并发也只会清一次（审查 O6）。
    reset_period: Mapped[str | None] = mapped_column(String(7), nullable=True)


def ensure_quota(db: Session, yard_id: str) -> YardQuota:
    quota = db.query(YardQuota).filter(YardQuota.yard_id == yard_id).first()
    if quota is not None:
        return quota
    quota = YardQuota(yard_id=yard_id)
    db.add(quota)
    db.flush()
    return quota


def legacy_calls_mode(db: Session, yard_id: str) -> str:
    """旧「模型调用次数」配额的模式：enforce / warn / off（v3.32 起由设置控制）。

    默认 warn：Token 额度接管硬约束后，旧的 1000 次上限只保留可见性与告警，
    不至于在新额度还很充裕时先把人拦在门外。
    """
    from app.modules.yards.settings_service import get_setting

    try:
        value = get_setting(db, yard_id, "quota").get("legacy_calls_mode")
    except Exception:  # 设置读取失败不阻断调用，按告警模式处理
        return "warn"
    return str(value or "warn")


def check_and_consume(
    db: Session, yard_id: str, kind: str, amount: int = 1, mode: str = "enforce"
) -> None:
    """原子扣减：判断与累加在同一条 UPDATE 内完成，并发请求不会超卖或丢计数。

    旧实现是「读-判断-写回」，两个并发请求都读到 used=N 时各自写 N+1，
    实际只记一次；改成条件 UPDATE 后由数据库保证串行语义。

    mode（v3.32）：enforce=超限拒绝（默认）；warn=照常计数与告警但不拦；off=不计数。
    """
    fields = _QUOTA_FIELDS.get(kind)
    if fields is None:
        raise AppError("INVALID_REQUEST", f"未知配额类型：{kind}。", http_status=400)
    if amount < 1:
        raise AppError("INVALID_REQUEST", "配额扣减数量必须为正整数。", http_status=400)
    if mode == "off":
        return
    ensure_quota(db, yard_id)
    used_attr, limit_attr = fields
    used_col = getattr(YardQuota, used_attr)
    limit_col = getattr(YardQuota, limit_attr)
    if mode == "warn":
        db.execute(
            update(YardQuota)
            .where(YardQuota.yard_id == yard_id)
            .values({used_attr: used_col + amount})
            .execution_options(synchronize_session=False)
        )
        used, limit = db.execute(
            select(used_col, limit_col).where(YardQuota.yard_id == yard_id)
        ).one()
        db.commit()
        _alert_thresholds(yard_id, kind, int(used), int(limit))
        return
    result = db.execute(
        update(YardQuota)
        .where(
            YardQuota.yard_id == yard_id,
            used_col + amount <= limit_col,
        )
        .values({used_attr: used_col + amount})
        .execution_options(synchronize_session=False)
    )
    # 读取扣减后的真实用量：直接查列，避免命中身份映射里的旧值。
    used, limit = db.execute(select(used_col, limit_col).where(YardQuota.yard_id == yard_id)).one()
    if not getattr(result, "rowcount", 0):
        label = KIND_LABELS.get(kind, kind)
        raise AppError(
            "QUOTA_EXCEEDED",
            f"{label}配额已用尽（已用 {used}/{limit}）。"
            "可由小院负责人/管理员在「管理与统计 → 用量额度」调整上限或清零已用。",
            http_status=429,
            details={"kind": kind, "label": label, "used": used, "limit": limit},
        )
    db.commit()
    _alert_thresholds(yard_id, kind, int(used), int(limit))


def _alert_thresholds(yard_id: str, kind: str, new_used: int, limit: int) -> None:
    """跨越 90%/100% 阈值时发运维告警（规范 §16.2）。

    dedupe_key 含阈值档位：同一进程内同档位只推一次，防止刷屏；告警失败不影响业务。
    """
    if limit <= 0:
        return
    label = KIND_LABELS.get(kind, kind)
    if new_used >= limit:
        send_alert(
            title="quota",
            message=f"小院 {yard_id} 的{label}配额已用尽（{new_used}/{limit}）。",
            level="critical",
            dedupe_key=f"quota:{yard_id}:{kind}:100",
        )
    elif new_used / limit * 100 >= WARNING_THRESHOLD:
        send_alert(
            title="quota",
            message=(
                f"小院 {yard_id} 的{label}使用率已达 "
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
        label = KIND_LABELS.get(kind, kind)
        percent = round(used / limit * 100, 2) if limit > 0 else 0.0
        items[kind] = {
            "label": label,
            "limit": limit,
            "used": used,
            "remaining": max(limit - used, 0),
            "usage_percent": percent,
        }
        if percent >= WARNING_THRESHOLD:
            warnings.append(
                {
                    "kind": kind,
                    "label": label,
                    "level": "warning",
                    "message": f"{label}配额使用率已达 {percent}%（{used}/{limit}）。",
                }
            )
    return {
        "yard_id": yard_id,
        "period": current_period(),
        "quotas": items,
        "warnings": warnings,
    }


def reset_usage(db: Session, yard_id: str, kinds: list[str] | None = None) -> dict:
    """把已用量清零（只动 used，不动 limit）。

    月度清零依赖 Redis beat 任务，本地/桌面部署没有 Redis 时永远不会执行；
    因此给运营侧一个显式入口，超限后可以立刻恢复可用。
    """
    targets = list(dict.fromkeys(kinds or list(_QUOTA_FIELDS)))
    unknown = [k for k in targets if k not in _QUOTA_FIELDS]
    if unknown:
        raise AppError(
            "INVALID_REQUEST",
            f"未知配额类型：{'、'.join(unknown)}。",
            http_status=400,
        )
    ensure_quota(db, yard_id)
    db.execute(
        update(YardQuota)
        .where(YardQuota.yard_id == yard_id)
        .values({_QUOTA_FIELDS[k][0]: 0 for k in targets})
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return get_quota_status(db, yard_id)
