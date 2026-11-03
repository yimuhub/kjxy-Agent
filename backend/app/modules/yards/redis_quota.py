"""跨进程日配额计数（v3.56，审计 F-5）。

问题：搜索/技能生成的日配额原先是进程内 dict，``uvicorn --workers N`` 或多副本
部署时配额 ×N 失效。本模块用 Redis ``INCR + 当日过期`` 做跨进程计数：

- 键：``quota:{key}:{UTC 日期}``，过期 2 天（跨过午夜仍能读到当日值）；
- Redis 不可用（桌面单机/连接抖动）→ 返回 None，调用方回落进程内 dict，
  行为与旧实现一致——**计数器升级绝不引入新的启动依赖**；
- key 由调用方自带命名空间（如 ``search:{yard_id}``、``skill_gen:{yard_id}``）。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.core.config import settings

log = logging.getLogger(__name__)

_PREFIX = "quota"
_TTL_SECONDS = 172800  # 2 天


def _redis_client():
    """惰性取共享 Redis 连接；未配置/不可用返回 None（调用方回落）。"""
    try:
        import redis  # noqa: PLC0415 - 惰性导入：无 Redis 部署不触发

        url = settings.celery_broker_url
        if not url or not url.startswith(("redis://", "rediss://")):
            return None
        client = getattr(_redis_client, "_client", None)
        if client is None:
            client = redis.Redis.from_url(url, socket_connect_timeout=0.5, socket_timeout=0.5)
            _redis_client._client = client  # type: ignore[attr-defined]
        client.ping()
        return client
    except Exception as e:  # noqa: BLE001 - Redis 不可用是常态路径（桌面单机）
        log.debug("redis quota unavailable: %s", e)
        return None


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def incr_daily(key: str, amount: int = 1) -> int | None:
    """当日计数 +amount，返回最新值；Redis 不可用返回 None。"""
    client = _redis_client()
    if client is None:
        return None
    try:
        name = f"{_PREFIX}:{key}:{_today()}"
        value = int(client.incrby(name, amount))
        client.expire(name, _TTL_SECONDS)
        return value
    except Exception as e:  # noqa: BLE001
        log.debug("redis incr failed key=%s: %s", key, e)
        return None


def get_daily(key: str) -> int | None:
    """读当日计数；Redis 不可用返回 None。"""
    client = _redis_client()
    if client is None:
        return None
    try:
        value = client.get(f"{_PREFIX}:{key}:{_today()}")
        return int(value) if value is not None else 0
    except Exception as e:  # noqa: BLE001
        log.debug("redis get failed key=%s: %s", key, e)
        return None


__all__ = ["get_daily", "incr_daily"]
