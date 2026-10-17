"""Worker 心跳（规范 §18.4 worker 健康不能用 API 健康代替）。

设计：Worker 每完成/开始任务时刷新一个带 TTL 的 Redis 键；API 侧 /health/worker
读取该键判断最近是否有 worker 活动。Redis 不可用时降级为 unknown（不误报 ready）。
"""

import time

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

_HEARTBEAT_KEY = "techyard:worker:heartbeat"
# TTL 需大于 worker 轮询间隔；3 分钟内无心跳视为 worker 可能停摆
_TTL_SECONDS = 180


def _client():
    import redis

    return redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)


def mark_worker_alive() -> None:
    """由 Worker 进程调用，刷新心跳；失败不抛出（心跳不应影响任务执行）。"""
    try:
        _client().set(_HEARTBEAT_KEY, str(int(time.time())), ex=_TTL_SECONDS)
    except Exception as e:  # Redis 不可用等：静默降级
        log.debug("worker heartbeat mark failed: %s", e)


def worker_heartbeat_status() -> dict[str, object]:
    """API 侧读取心跳，返回结构化状态（供 /health/worker）。"""
    try:
        raw = _client().get(_HEARTBEAT_KEY)
    except Exception:
        return {"worker": "unknown", "reason": "redis_unavailable", "age_seconds": None}
    if raw is None:
        return {"worker": "stale", "reason": "no_heartbeat_within_ttl", "age_seconds": None}
    try:
        ts = int(raw)
    except (TypeError, ValueError):
        return {"worker": "unknown", "reason": "corrupt_heartbeat", "age_seconds": None}
    age = int(time.time()) - ts
    status = "alive" if 0 <= age <= _TTL_SECONDS else "stale"
    return {"worker": status, "reason": "", "age_seconds": age}
