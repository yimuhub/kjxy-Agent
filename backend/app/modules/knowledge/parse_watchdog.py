"""知识库解析看门狗（review D-03 残留场景）。

背景
----
``knowledge/service.py::_dispatch_parse`` 是全后端唯一的 Celery 投递点，它已经覆盖
两种降级：

1. ``TASKS_INLINE_MODE=true``（桌面单机）：不投递，本机后台线程直接解析；
2. broker 不可达且 ``WORKER_SYNC_FALLBACK=true``：投递失败后本机兜底。

但**第三种场景此前无人兜底**：``.delay()`` 投递成功（消息进了队列），文档状态却
永远停在 ``queued``——用户看到"永远解析中"，没有任何报错，也没有任何自愈入口。
实测有两种成因：

- **A. 没有 worker 消费**：broker 可达但 worker 进程没起（部署漏配 / worker 崩了）；
- **B. worker 消费了但拿不到这份文档**：worker 与 API 指向了**不同的数据库**
  （本地走查环境正是如此：celery worker 连 SQLite 库，API 连 PostgreSQL 库）。
  worker 取走消息后在自己的库里找不到文档，直接丢弃，消息被 ack，
  API 这边的文档就永远停在 ``queued``。

两种成因的共同特征都是"文档停在 queued 且没人推进"，因此看门狗按**文档状态**
判定，而不是按"投递是否成功"判定。

超时阈值分两档
--------------
用 worker 心跳（``app/workers/heartbeat.py``，TTL 180 秒）区分"没人干活"与
"有人但很慢"。但判定"有人干活"不能只看心跳是否存在，必须确认这个 worker
**真的能消费本 API 投递的消息**：

- 心跳必须来自**真实 Celery worker**（``role=celery_worker``）——API 进程自己的
  内联兜底执行器不是 worker，它写的心跳不构成"有人在消费队列"的证据；
- worker 连的必须与 API 是**同一个数据库**——否则消息会被取走后丢弃（成因 B）。

任一条不满足即视为"没有可用 worker"：

- **无可用 worker** → 排队超过 ``WATCHDOG_QUEUED_STALE_SECONDS``（180s）即兜底；
- **有可用 worker** → 说明队列里大概率有人在处理，只是慢；但若是成因 B
  （消息被错误配置的 worker 吃掉并丢弃），就再也不会有人推进这份文档，
  因此排队超过 ``WATCHDOG_QUEUED_ALIVE_WORKER_STALE_SECONDS``（600s）也兜底。

兜底动作是**本机解析**（API 进程自己跑完整解析流程），而不是重新投递——
重新投递只会让同一份消息再被错误配置的 worker 吃掉一次。

幂等性
------
看门狗与真 worker 可能同时盯上同一份文档（心跳刚过期、worker 又活过来）。
因此 ``parse_document_sync`` 开头用**条件 UPDATE 做 CAS 抢占**：只有把状态从
``queued`` 抢到 ``processing`` 成功的一方才继续解析，另一方直接返回。
配合"解析前先删旧切块"，重复触发也不会产生重复 chunk、不会重复计费。

僵尸回收
--------
``processing`` 停太久（解析进程被 kill / OOM）同样需要自愈：看门狗把它重置回
``queued`` 并重新排队。
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.notify import send_alert
from app.modules.knowledge.models import KnowledgeDocument

log = get_logger(__name__)

#: 单轮最多兜底几份文档：避免积压时一轮拉起过多线程。
WATCHDOG_BATCH_LIMIT = 20

#: 巡检节奏（秒）——默认值，实际取值以 settings 为准。
WATCHDOG_INTERVAL_SECONDS = 60

#: 无活跃 worker 时，文档排队多久判定为"疑似无人消费"（秒）。
#: 必须显著大于正常解析耗时（大文档 + 向量化可能几十秒）。
WATCHDOG_QUEUED_STALE_SECONDS = 180

#: 有活跃 worker 时，文档排队多久仍不推进就判定为"消息已被丢弃"（秒）。
#: 这一档要更长，给正常积压留出消化时间。
WATCHDOG_QUEUED_ALIVE_WORKER_STALE_SECONDS = 600

#: processing 停多久判定为"僵尸解析"（解析进程被 kill / OOM）（秒）。
WATCHDOG_PROCESSING_STALE_SECONDS = 900

_started = False
_lock = threading.Lock()
_stop_event = threading.Event()


def _interval_seconds() -> int:
    return max(5, int(settings.knowledge_parse_watchdog_interval_seconds))


def _queued_stale_seconds() -> int:
    return max(30, int(settings.knowledge_parse_queued_stale_seconds))


def _queued_alive_worker_stale_seconds() -> int:
    return max(60, int(settings.knowledge_parse_queued_alive_worker_stale_seconds))


def _processing_stale_seconds() -> int:
    return max(60, int(settings.knowledge_parse_processing_stale_seconds))


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _stale_queued_ids(db: Session, *, now: datetime, stale_seconds: int, limit: int) -> list[str]:
    """找出排队超时、疑似无人推进的文档。

    判定依据是 ``updated_at``：文档被投递后状态一直是 ``queued``，
    ``updated_at`` 不会再变，因此它就是"入队时刻"。
    """
    cutoff = now - timedelta(seconds=stale_seconds)
    rows = db.execute(
        select(KnowledgeDocument.id)
        .where(
            KnowledgeDocument.status == "queued",
            KnowledgeDocument.deleted_at.is_(None),
            KnowledgeDocument.updated_at < cutoff,
        )
        .limit(limit)
    ).scalars()
    return [str(r) for r in rows]


def _stale_processing_ids(db: Session, *, now: datetime, limit: int) -> list[str]:
    """找出卡在 processing 的僵尸文档（解析进程被 kill / OOM）。"""
    cutoff = now - timedelta(seconds=_processing_stale_seconds())
    rows = db.execute(
        select(KnowledgeDocument.id)
        .where(
            KnowledgeDocument.status == "processing",
            KnowledgeDocument.deleted_at.is_(None),
            KnowledgeDocument.updated_at < cutoff,
        )
        .limit(limit)
    ).scalars()
    return [str(r) for r in rows]


def _worker_alive() -> bool:
    """是否存在**能消费本 API 消息**的活 worker。

    仅"有心跳"不够，必须同时满足三个条件：

    1. 心跳键存在且未过期；
    2. 心跳由**真实 Celery worker** 写入（``role=celery_worker``）——
       API 进程自己的内联兜底执行器不是 worker，它活着只说明 API 在动，
       队列里的消息仍然没人消费；
    3. worker 连的是**同一个数据库**——worker 与 API 连不同库时，
       消息会被取走后丢弃，永远不会推进本 API 的文档（审查 D-03 场景 C）。

    任何一条不满足都按"没有可用 worker"处理，走较短的那档超时兜底。
    心跳读不出来（redis 挂了）时同样返回 False，这是**保守**选择：看门狗会走
    较短的那档超时兜底，代价是最多一次重复解析（CAS 抢占保证不会真重复），
    收益是文档不会永久卡死。
    """
    try:
        from app.workers.heartbeat import worker_heartbeat_status

        status = worker_heartbeat_status()
        if status.get("worker") != "alive":
            return False
        if not status.get("usable"):
            log.debug(
                "心跳存在但不可用于本 API：role=%s reason=%s worker_db=%s api_db=%s",
                status.get("role"),
                status.get("reason"),
                status.get("db"),
                _api_db_fingerprint(),
            )
            return False
        return True
    except Exception as e:  # noqa: BLE001 - 心跳不可用不应让看门狗崩掉
        log.debug("worker heartbeat read failed: %s", e)
        return False


def _api_db_fingerprint() -> str:
    try:
        from app.workers.heartbeat import current_db_fingerprint

        return current_db_fingerprint()
    except Exception:  # noqa: BLE001
        return "unknown"


def _parse_now(document_id: str) -> None:
    """本机兜底解析一份文档（自建会话，不依赖请求上下文）。"""
    from app.db.session import SessionLocal
    from app.modules.knowledge.worker_tasks import parse_document_sync

    db = SessionLocal()
    try:
        parse_document_sync(db, document_id)
    except Exception:
        log.exception("看门狗兜底解析异常 document_id=%s", document_id)
    finally:
        db.close()


def _run_in_thread(document_id: str) -> None:
    threading.Thread(
        target=_parse_now,
        args=(document_id,),
        name=f"parse-watchdog-{document_id[:8]}",
        daemon=True,
    ).start()


def sweep_once(db: Session) -> dict[str, int]:
    """执行一轮巡检。返回 {"rescued": n, "zombie_reset": n}。

    幂等：同一份文档被多轮巡检命中也只会解析一次（CAS 抢占）。
    """
    stats = {"rescued": 0, "zombie_reset": 0}
    now = _utcnow()

    # 僵尸 processing 优先：这类文档既不会自愈，也不会被 worker 捡起来。
    for document_id in _stale_processing_ids(db, now=now, limit=WATCHDOG_BATCH_LIMIT):
        result = db.execute(
            update(KnowledgeDocument)
            .where(
                KnowledgeDocument.id == document_id,
                KnowledgeDocument.status == "processing",
            )
            .values(status="queued", error_message="解析进程中断，已由看门狗重新排队。")
        )
        db.commit()
        # UPDATE 语句返回的是 CursorResult（带 rowcount）；泛型 Result 上没有该属性。
        if cast(CursorResult, result).rowcount:
            stats["zombie_reset"] += 1
            log.warning("看门狗回收僵尸文档 document_id=%s", document_id)

    alive = _worker_alive()
    stale_seconds = _queued_alive_worker_stale_seconds() if alive else _queued_stale_seconds()
    queued_ids = _stale_queued_ids(
        db, now=now, stale_seconds=stale_seconds, limit=WATCHDOG_BATCH_LIMIT
    )
    if not queued_ids:
        return stats

    log.warning(
        "看门狗：%d 份文档排队超时（worker_alive=%s），改为本机兜底解析",
        len(queued_ids),
        alive,
    )
    for document_id in queued_ids:
        _run_in_thread(document_id)
        stats["rescued"] += 1

    send_alert(
        "知识库解析长时间无进展",
        f"{len(queued_ids)} 份文档排队超过 {stale_seconds}s 仍无进展"
        f"（worker_alive={alive}），已由看门狗本机兜底解析。",
        level="warning",
        dedupe_key="knowledge.parse.stalled",
    )
    return stats


def _loop() -> None:
    log.info("knowledge parse watchdog started (interval=%ss)", _interval_seconds())
    while True:
        if _stop_event.wait(timeout=_interval_seconds()):
            return
        try:
            from app.db.session import SessionLocal

            db = SessionLocal()
            try:
                sweep_once(db)
            finally:
                db.close()
        except Exception:
            log.exception("knowledge parse watchdog sweep failed")


def start_parse_watchdog() -> bool:
    """启动看门狗线程（幂等）。返回是否新启动。

    桌面单机模式（``TASKS_INLINE_MODE=true``）**不需要**看门狗：解析从来不进队列，
    不存在"无人消费"的可能；强行启动只会做无意义的巡检。

    ``_started`` 只在**真的拉起线程**时才置位：此前 inline/禁用路径也置 True，
    ``watchdog_status()["running"]`` 就会虚报 true——心跳/存活信号出现假阳性，
    恰是 D-03 第二层根因（API 冒充 worker）教训的重演。
    """
    global _started
    with _lock:
        if _started:
            return False
        if settings.tasks_inline_mode or not settings.knowledge_parse_watchdog_enabled:
            return False
        _started = True

    thread = threading.Thread(target=_loop, name="knowledge-parse-watchdog", daemon=True)
    thread.start()
    return True


def stop_parse_watchdog() -> None:
    """停止看门狗线程（测试用）。"""
    global _started
    _stop_event.set()
    with _lock:
        _started = False


def watchdog_status() -> dict[str, object]:
    """看门狗状态（供 /health 探针与运维排查）。"""
    heartbeat: dict[str, object] = {}
    try:
        from app.workers.heartbeat import worker_heartbeat_status

        heartbeat = worker_heartbeat_status()
    except Exception:  # noqa: BLE001 - 探针不应因心跳不可用而失败
        heartbeat = {}
    return {
        "running": _started and not _stop_event.is_set(),
        "enabled": settings.knowledge_parse_watchdog_enabled,
        "interval_seconds": _interval_seconds(),
        "queued_stale_seconds": _queued_stale_seconds(),
        "queued_alive_worker_stale_seconds": _queued_alive_worker_stale_seconds(),
        "processing_stale_seconds": _processing_stale_seconds(),
        "worker_alive": _worker_alive(),
        "worker_heartbeat": heartbeat,
        "api_db_fingerprint": _api_db_fingerprint(),
    }


__all__ = [
    "start_parse_watchdog",
    "stop_parse_watchdog",
    "sweep_once",
    "watchdog_status",
    "WATCHDOG_INTERVAL_SECONDS",
]
