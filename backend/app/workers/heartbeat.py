"""Worker 心跳（规范 §18.4 worker 健康不能用 API 健康代替）。

设计：Worker 每完成/开始任务时刷新一个带 TTL 的 Redis 键；API 侧 /health/worker
读取该键判断最近是否有 worker 活动。Redis 不可用时降级为 unknown（不误报 ready）。

心跳必须带身份（审查 D-03 场景 C 的真实复现）
--------------------------------------------
只看"有没有心跳"不足以回答看门狗真正想问的问题——**队列里那条消息会不会被消费**：

- **API 进程自己的内联兜底执行器也会写心跳**。它是 API 进程，不是 worker，
  写进 worker 心跳键会造成"有活 worker"的假阳性，看门狗据此把兜底阈值从
  180s 拉到 600s，文档实打实多等十分钟。因此内联执行器改写**独立键**。
- **worker 与 API 可能连的不是同一个库**。此时 worker 取走消息后在自己的库里
  找不到文档，直接 ack 丢弃，API 这边的文档永远停在 queued。心跳里必须带上
  **库指纹**，看门狗才能发现"这个 worker 帮不了我"。

因此心跳值是 JSON：``{"ts": …, "role": …, "db": <库指纹>}``：

- ``role``：``celery_worker`` 表示真实 worker 进程；``inline_api`` 表示 API 内联执行器；
- ``db``：当前进程所连数据库的指纹（哈希，不含口令），用于判断是否同一个库。

旧格式（裸时间戳）没有身份信息，一律按"不可用"处理——宁可多兜底一次
（CAS 抢占保证不会真重复解析），也不让文档永久卡死。
"""

import hashlib
import json
import time

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

#: 真实 Celery worker 的心跳键。只有 worker 进程可以写。
_HEARTBEAT_KEY = "techyard:worker:heartbeat"
#: API 进程内联兜底执行器的心跳键。它**不是** worker，单独放一个键。
_INLINE_KEY = "techyard:api:inline:heartbeat"

# TTL 需大于 worker 轮询间隔；3 分钟内无心跳视为 worker 可能停摆
_TTL_SECONDS = 180

#: 心跳角色常量。
ROLE_WORKER = "celery_worker"
ROLE_INLINE_API = "inline_api"


def _client():
    import redis

    return redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)


def current_db_fingerprint() -> str:
    """当前进程所连数据库的指纹（哈希，不含口令）。

    只比较**数据库身份**（后端类型 + 主机 + 端口 + 库名），不比较驱动串——
    ``postgresql://`` 与 ``postgresql+psycopg://`` 指向同一个库，指纹必须一致。
    SQLite 走文件路径，路径不同即为不同的库。
    """
    try:
        from sqlalchemy.engine import make_url

        url = make_url(settings.database_url)
        ident = f"{url.get_backend_name()}://{url.host or ''}:{url.port or ''}/{url.database or ''}"
    except Exception:  # noqa: BLE001 - 指纹失败时退化为原始串，仍然可用
        ident = settings.database_url
    return hashlib.sha256(ident.encode("utf-8")).hexdigest()[:12]


def _write(key: str, role: str) -> None:
    payload = json.dumps(
        {"ts": int(time.time()), "role": role, "db": current_db_fingerprint()},
        ensure_ascii=False,
    )
    try:
        _client().set(key, payload, ex=_TTL_SECONDS)
    except Exception as e:  # Redis 不可用等：静默降级
        log.debug("heartbeat mark failed (%s): %s", role, e)


def mark_worker_alive() -> None:
    """由**真实 Worker 进程**调用，刷新心跳；失败不抛出（心跳不应影响任务执行）。"""
    _write(_HEARTBEAT_KEY, ROLE_WORKER)


def mark_inline_alive() -> None:
    """由 **API 进程内的内联兜底执行器**调用。

    刻意与 worker 心跳分开：内联执行器跑在 API 进程里，它"活着"只说明
    API 在动，不代表有 worker 会消费队列里的消息。
    """
    _write(_INLINE_KEY, ROLE_INLINE_API)


def _parse_payload(raw: str | bytes) -> dict[str, object] | None:
    """解析心跳载荷；旧格式（裸时间戳）返回 None。"""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or "ts" not in data:
        return None
    return data


def worker_heartbeat_status() -> dict[str, object]:
    """API 侧读取心跳，返回结构化状态（供 /health/worker 与看门狗）。

    ``worker``：有没有 worker 进程在打心跳。
    ``usable``：这个 worker **能不能消费本 API 投递的消息**——
    必须是真实 worker（``role=celery_worker``）且连的是**同一个数据库**。
    看门狗只认 ``usable``。
    """
    try:
        raw = _client().get(_HEARTBEAT_KEY)
    except Exception:
        return {
            "worker": "unknown",
            "reason": "redis_unavailable",
            "age_seconds": None,
            "role": None,
            "db": None,
            "usable": False,
        }
    if raw is None:
        return {
            "worker": "stale",
            "reason": "no_heartbeat_within_ttl",
            "age_seconds": None,
            "role": None,
            "db": None,
            "usable": False,
        }
    payload = _parse_payload(raw)
    if payload is None:
        return {
            "worker": "unknown",
            "reason": "legacy_heartbeat_format",
            "age_seconds": None,
            "role": None,
            "db": None,
            "usable": False,
        }
    try:
        raw_ts = payload["ts"]
        if not isinstance(raw_ts, int | float | str):
            raise TypeError("heartbeat ts 不是数值")
        ts = int(raw_ts)
    except (TypeError, ValueError):
        return {
            "worker": "unknown",
            "reason": "corrupt_heartbeat",
            "age_seconds": None,
            "role": None,
            "db": None,
            "usable": False,
        }
    age = int(time.time()) - ts
    role = payload.get("role")
    db = payload.get("db")
    if not (0 <= age <= _TTL_SECONDS):
        return {
            "worker": "stale",
            "reason": "heartbeat_expired",
            "age_seconds": age,
            "role": role,
            "db": db,
            "usable": False,
        }
    usable = role == ROLE_WORKER and bool(db) and db == current_db_fingerprint()
    reason = ""
    if not usable:
        reason = "role_not_worker" if role != ROLE_WORKER else "worker_on_other_database"
    return {
        "worker": "alive",
        "reason": reason,
        "age_seconds": age,
        "role": role,
        "db": db,
        "usable": usable,
    }


__all__ = [
    "ROLE_INLINE_API",
    "ROLE_WORKER",
    "current_db_fingerprint",
    "mark_inline_alive",
    "mark_worker_alive",
    "worker_heartbeat_status",
]
