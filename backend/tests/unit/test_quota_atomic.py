"""配额扣减原子性（3.7）：条件 UPDATE 防超卖、防丢计数。

用文件型 SQLite + 两个独立 Session 模拟并发请求持旧快照的场景：
旧实现「读-判断-写回」会用内存里的旧 used 覆盖数据库，导致少记；
新实现由 `WHERE used + amount <= limit` 在数据库内完成判断与累加。
"""

import pytest
from app.core.errors import AppError
from app.db.base import Base
from app.modules.yards import quota as quota_mod
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def sessions(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'quota.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(bind=engine)
    # expire_on_commit=False：commit 只结束事务，保留身份映射里的旧值，
    # 以便模拟并发请求已读到的陈旧快照。
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    first, second = factory(), factory()
    try:
        yield first, second
    finally:
        first.close()
        second.close()
        engine.dispose()


def _prime(session, yard_id: str, limit: int) -> None:
    q = quota_mod.ensure_quota(session, yard_id)
    q.tasks_limit = limit
    session.commit()
    loaded = session.query(quota_mod.YardQuota).filter_by(yard_id=yard_id).one()
    assert loaded.tasks_used == 0
    session.commit()  # 结束读事务释放锁，对象因 expire_on_commit=False 仍是旧值


def _used(session, yard_id: str) -> int:
    return int(
        session.execute(
            select(quota_mod.YardQuota.tasks_used).where(quota_mod.YardQuota.yard_id == yard_id)
        ).scalar_one()
    )


def test_stale_reader_cannot_bypass_limit(sessions):
    stale, other = sessions
    yard = "yard-stale"
    _prime(stale, yard, limit=1)

    quota_mod.check_and_consume(other, yard, "tasks")  # 另一请求用掉最后一个名额

    with pytest.raises(AppError) as exc:
        quota_mod.check_and_consume(stale, yard, "tasks")
    assert exc.value.code == "QUOTA_EXCEEDED"
    assert exc.value.details == {"kind": "tasks", "label": "任务", "used": 1, "limit": 1}
    assert _used(other, yard) == 1


def test_stale_reader_increment_is_not_lost(sessions):
    stale, other = sessions
    yard = "yard-lost-update"
    _prime(stale, yard, limit=2)

    quota_mod.check_and_consume(other, yard, "tasks")
    quota_mod.check_and_consume(stale, yard, "tasks")  # 旧快照里仍是 0

    assert _used(other, yard) == 2


def test_batch_amount_respects_remaining(sessions):
    db, _ = sessions
    yard = "yard-batch"
    _prime(db, yard, limit=5)

    quota_mod.check_and_consume(db, yard, "tasks", amount=3)
    with pytest.raises(AppError) as exc:
        quota_mod.check_and_consume(db, yard, "tasks", amount=3)
    assert exc.value.code == "QUOTA_EXCEEDED"
    assert exc.value.details == {"kind": "tasks", "label": "任务", "used": 3, "limit": 5}
    assert _used(db, yard) == 3


def test_rejects_unknown_kind_and_non_positive_amount(sessions):
    db, _ = sessions
    with pytest.raises(AppError) as bad_kind:
        quota_mod.check_and_consume(db, "yard-bad", "tokens")
    assert bad_kind.value.code == "INVALID_REQUEST"

    with pytest.raises(AppError) as bad_amount:
        quota_mod.check_and_consume(db, "yard-bad", "tasks", amount=0)
    assert bad_amount.value.code == "INVALID_REQUEST"
