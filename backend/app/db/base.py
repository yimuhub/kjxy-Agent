"""SQLAlchemy 基类：统一 UUID 主键、UTC 时间戳、软删除字段约定。"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(UTC)


def normalize_vector(value: Any) -> list[float] | None:
    """把任意方言下读回的向量值统一收敛为 ``list[float] | None``。

    背景（生产事故）：PostgreSQL + pgvector 的 ``Vector`` 列读回是
    ``numpy.ndarray``，而 SQLite/JSON 回退读回是 ``list``。业务代码里
    ``if not vec:`` 对 ndarray 会抛
    ``ValueError: The truth value of an array with more than one element is ambiguous``，
    于是 SQLite 测试全绿、生产库检索直接 500。

    这里在**类型层**一次性收敛，避免业务层散落的真值判断再次踩坑。
    """
    if value is None:
        return None
    if isinstance(value, list):
        return [float(v) for v in value]
    if isinstance(value, tuple):
        return [float(v) for v in value]
    # numpy.ndarray / torch.Tensor 等：走 tolist()
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        try:
            converted = tolist()
        except Exception:  # noqa: BLE001 - 退化到逐元素转换
            converted = None
        if isinstance(converted, list):
            return [float(v) for v in converted]
    try:
        return [float(v) for v in value]
    except (TypeError, ValueError):
        return None


def is_empty_vector(value: Any) -> bool:
    """向量是否缺失或为空——**唯一**判定向量存在性的入口。

    禁止再写 ``if not vec`` / ``if vec``：ndarray 会直接抛 ValueError。
    """
    if value is None:
        return True
    try:
        return len(value) == 0
    except TypeError:
        return True


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
