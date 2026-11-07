"""超时回收（reaper）单元测试（规范 §7.5）：queued/running 超时 → failed。

直接调用 heartbeat_tasks.reap_once（同一内存库会话）；另验证 beat 注册与
celery 入口 reap_stale_tasks 的会话工厂注入。
"""

from datetime import UTC, datetime, timedelta

import pytest
from app.db.base import Base  # noqa: F401  确保 metadata 完整
from app.modules.audit.models import AuditLog  # noqa: F401  注册模型
from app.modules.tasks import state_machine as sm
from app.modules.tasks.models import Task, TaskEvent  # noqa: F401  注册模型
from app.workers import heartbeat_tasks
from app.workers.celery_app import celery_app
from sqlalchemy.orm import sessionmaker


def _make_task(
    db,
    *,
    status: str,
    age_minutes: int,
    yard: str = "t-reap",
    task_type: str = "mock.echo",
) -> Task:
    ts = datetime.now(UTC) - timedelta(minutes=age_minutes)
    task = Task(
        yard_id=yard,
        created_by="u-reap",
        task_type=task_type,
        status=status,
        input_json={"data": {}, "meta": {}},
        created_at=ts,
        updated_at=ts,
    )
    db.add(task)
    db.commit()
    return task


def _events_for(db, task_id: str) -> list[TaskEvent]:
    return list(db.query(TaskEvent).filter(TaskEvent.task_id == task_id))


def test_reap_queued_timeout_only_touches_stale(db_session):
    stale = _make_task(db_session, status=sm.STATUS_QUEUED, age_minutes=61)
    fresh = _make_task(db_session, status=sm.STATUS_QUEUED, age_minutes=5)

    stats = heartbeat_tasks.reap_once(db_session)
    assert stats["queued_timeout"] == 1
    assert stats["running_timeout"] == 0

    db_session.expire_all()
    assert stale.status == sm.STATUS_FAILED
    assert stale.error_code == "QUEUED_TIMEOUT"
    assert stale.finished_at is not None
    assert fresh.status == sm.STATUS_QUEUED
    # 每条回收必须留下事件，可追溯
    messages = [e.message for e in _events_for(db_session, stale.id)]
    assert any("回收" in m for m in messages)
    audit = db_session.query(AuditLog).filter(AuditLog.action == "task.reap").count()
    assert audit == 1


def test_reap_running_timeout_uses_updated_at(db_session):
    stale = _make_task(db_session, status=sm.STATUS_RUNNING, age_minutes=181)
    fresh = _make_task(db_session, status=sm.STATUS_RUNNING, age_minutes=10)

    stats = heartbeat_tasks.reap_once(db_session)
    assert stats["queued_timeout"] == 0
    assert stats["running_timeout"] == 1

    db_session.expire_all()
    assert stale.status == sm.STATUS_FAILED
    assert stale.error_code == "RUNNING_TIMEOUT"
    assert fresh.status == sm.STATUS_RUNNING
    events = _events_for(db_session, stale.id)
    assert any(e.event_type == "status_changed" for e in events)
    payload = events[-1].payload_json or {}
    assert payload.get("from") == sm.STATUS_RUNNING
    assert payload.get("to") == sm.STATUS_FAILED


def test_reap_empty_is_noop(db_session):
    stats = heartbeat_tasks.reap_once(db_session)
    assert stats == {"queued_timeout": 0, "running_timeout": 0}


def test_reap_celery_entry_uses_injected_session_factory(db_session, session_factory_from_db):
    stale = _make_task(db_session, status=sm.STATUS_QUEUED, age_minutes=200)
    heartbeat_tasks._session_factory = session_factory_from_db
    try:
        stats = heartbeat_tasks.reap_stale_tasks()
    finally:
        heartbeat_tasks._session_factory = None
    assert stats["queued_timeout"] == 1
    db_session.expire_all()
    assert stale.status == sm.STATUS_FAILED


def test_beat_schedule_registered():
    schedules = celery_app.conf.beat_schedule
    entries = {entry["task"]: entry["schedule"] for entry in schedules.values()}
    assert entries["tasks.reap_stale"] == 300.0  # 每 5 分钟
    assert entries["workers.heartbeat"] == 60.0  # 每 60 秒
    assert entries["quota.monthly_reset"] == 86400.0  # 每日检查、当月只清一次
    assert "tasks.reap_stale" in celery_app.tasks
    assert "workers.heartbeat" in celery_app.tasks
    # 桌面内嵌维护与 beat 的回收节奏保持一致（审查 O6）
    assert heartbeat_tasks.INLINE_REAP_SECONDS == 300


def test_legacy_quota_monthly_reset_is_idempotent(db_session):
    """月清零靠 reset_period 条件 UPDATE：当月只清一次，重复调用不再抹数据。"""
    from app.modules.yards.quota import YardQuota

    quota = YardQuota(
        yard_id="yard-reset",
        llm_calls_used=12,
        knowledge_docs_used=3,
        tasks_used=7,
        reset_period="2026-10",
    )
    db_session.add(quota)
    db_session.commit()

    assert heartbeat_tasks.reset_legacy_quota_periods(db_session, period="2026-11") == 1
    db_session.expire_all()
    assert (quota.llm_calls_used, quota.knowledge_docs_used, quota.tasks_used) == (0, 0, 0)
    assert quota.reset_period == "2026-11"

    # 同月再跑：没有命中行，不会把当月新产生的用量再清零
    quota.llm_calls_used = 4
    db_session.commit()
    assert heartbeat_tasks.reset_legacy_quota_periods(db_session, period="2026-11") == 0
    db_session.expire_all()
    assert quota.llm_calls_used == 4


def test_legacy_quota_reset_covers_never_reset_rows(db_session):
    """从旧版本升上来的行 reset_period 为 NULL，应被首次巡检清零并打上月份。"""
    from app.modules.yards.quota import YardQuota

    quota = YardQuota(yard_id="yard-null", llm_calls_used=5)
    db_session.add(quota)
    db_session.commit()

    assert heartbeat_tasks.reset_legacy_quota_periods(db_session, period="2026-11") == 1
    db_session.expire_all()
    assert quota.llm_calls_used == 0
    assert quota.reset_period == "2026-11"


@pytest.fixture()
def session_factory_from_db(db_session):
    engine = db_session.get_bind()
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)
