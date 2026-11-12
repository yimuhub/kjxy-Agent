"""Worker 心跳身份判定单测（审查 D-03 场景 C）。

要守住的性质：

1. 只有真实 Celery worker 写 worker 心跳键；API 内联执行器写独立键；
2. 心跳带库指纹，worker 与 API 连不同库时判定为**不可用**；
3. 旧格式（裸时间戳）没有身份，一律按不可用处理——宁可多兜底一次，
   也不让文档永久卡在 queued。
"""

from __future__ import annotations

import json
import time

import pytest
from app.workers import heartbeat as hb


class _FakeRedis:
    """最小 Redis 替身：只实现心跳模块用到的 set/get。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.store[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self.store.get(key)


@pytest.fixture
def fake_redis(monkeypatch):
    fr = _FakeRedis()
    monkeypatch.setattr(hb, "_client", lambda: fr)
    return fr


def test_mark_worker_alive_writes_worker_key(fake_redis):
    hb.mark_worker_alive()
    assert hb._HEARTBEAT_KEY in fake_redis.store
    payload = json.loads(fake_redis.store[hb._HEARTBEAT_KEY])
    assert payload["role"] == hb.ROLE_WORKER
    assert payload["db"] == hb.current_db_fingerprint()
    assert payload["ts"] <= int(time.time())


def test_mark_inline_alive_uses_separate_key(fake_redis):
    """内联执行器不得写 worker 心跳键。"""
    hb.mark_inline_alive()
    assert hb._HEARTBEAT_KEY not in fake_redis.store
    assert hb._INLINE_KEY in fake_redis.store


def test_worker_heartbeat_status_usable_when_same_database(fake_redis):
    hb.mark_worker_alive()
    status = hb.worker_heartbeat_status()
    assert status["worker"] == "alive"
    assert status["usable"] is True
    assert status["role"] == hb.ROLE_WORKER


def test_worker_heartbeat_status_not_usable_on_other_database(fake_redis, monkeypatch):
    """worker 连的是别的库 → 心跳"活着"但**不可用**（消息会被取走后丢弃）。"""
    hb.mark_worker_alive()
    monkeypatch.setattr(hb, "current_db_fingerprint", lambda: "deadbeef1234")
    status = hb.worker_heartbeat_status()
    assert status["worker"] == "alive"
    assert status["usable"] is False
    assert status["reason"] == "worker_on_other_database"


def test_worker_heartbeat_status_legacy_format_not_usable(fake_redis):
    """旧格式（裸时间戳）没有身份信息 → 不可用。"""
    fake_redis.store[hb._HEARTBEAT_KEY] = str(int(time.time()))
    status = hb.worker_heartbeat_status()
    assert status["usable"] is False
    assert status["reason"] == "legacy_heartbeat_format"


def test_worker_heartbeat_status_missing_key_is_stale(fake_redis):
    status = hb.worker_heartbeat_status()
    assert status["worker"] == "stale"
    assert status["usable"] is False


def test_worker_heartbeat_status_expired(fake_redis):
    fake_redis.store[hb._HEARTBEAT_KEY] = json.dumps(
        {"ts": int(time.time()) - 10_000, "role": hb.ROLE_WORKER, "db": hb.current_db_fingerprint()}
    )
    status = hb.worker_heartbeat_status()
    assert status["worker"] == "stale"
    assert status["usable"] is False


def test_inline_role_is_not_usable(fake_redis):
    """即使内联键被误当成 worker 键，role 不对也不可用。"""
    fake_redis.store[hb._HEARTBEAT_KEY] = json.dumps(
        {"ts": int(time.time()), "role": hb.ROLE_INLINE_API, "db": hb.current_db_fingerprint()}
    )
    status = hb.worker_heartbeat_status()
    assert status["worker"] == "alive"
    assert status["usable"] is False
    assert status["reason"] == "role_not_worker"


def test_db_fingerprint_ignores_driver_suffix(monkeypatch):
    """postgresql:// 与 postgresql+psycopg:// 指向同一个库，指纹必须一致。"""
    from app.core.config import settings

    monkeypatch.setattr(
        settings, "database_url", "postgresql+psycopg://u:p@127.0.0.1:55432/techyard"
    )
    a = hb.current_db_fingerprint()
    monkeypatch.setattr(settings, "database_url", "postgresql://u:p@127.0.0.1:55432/techyard")
    b = hb.current_db_fingerprint()
    assert a == b


def test_db_fingerprint_differs_across_databases(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(
        settings, "database_url", "postgresql+psycopg://u:p@127.0.0.1:55432/techyard"
    )
    a = hb.current_db_fingerprint()
    monkeypatch.setattr(
        settings, "database_url", "postgresql+psycopg://u:p@127.0.0.1:55432/techyard_fresh"
    )
    assert a != hb.current_db_fingerprint()


def test_watchdog_treats_other_database_worker_as_unusable(monkeypatch):
    """看门狗视角：worker 连别的库时必须按"没有可用 worker"处理。"""
    from app.modules.knowledge import parse_watchdog

    monkeypatch.setattr(
        hb,
        "worker_heartbeat_status",
        lambda: {
            "worker": "alive",
            "usable": False,
            "reason": "worker_on_other_database",
            "role": hb.ROLE_WORKER,
            "db": "other",
            "age_seconds": 1,
        },
    )
    assert parse_watchdog._worker_alive() is False


def test_watchdog_uses_alive_worker_threshold_only_when_usable(monkeypatch):
    """可用 worker → 走较长阈值；不可用 → 走较短阈值。"""
    from app.modules.knowledge import parse_watchdog

    monkeypatch.setattr(parse_watchdog, "_worker_alive", lambda: True)
    assert (
        parse_watchdog._queued_alive_worker_stale_seconds() > parse_watchdog._queued_stale_seconds()
    )
