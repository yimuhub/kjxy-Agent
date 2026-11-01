"""SQLite 写串行化：多线程多 Session 经 _safe_commit 累加同一行不丢更新。"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from app.db.base import Base
from app.modules.commander import service
from app.modules.commander.models import CommanderRun
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.orm import sessionmaker


def test_safe_commit_serializes_concurrent_sqlite_writes(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'commander-concurrent.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # noqa: ANN001 - SQLAlchemy 回调签名
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    seed = factory()
    run = CommanderRun(
        yard_id="yard-concurrent",
        session_id="s-concurrent",
        user_id="u-concurrent",
        goal="并发累加",
        plan_json={"fallback": False, "subtasks": []},
        status="queued",
        max_parallel=3,
        effective_parallel=3,
        degraded=False,
        tokens_total=0,
        latency_ms=0,
        roles_json=[],
    )
    seed.add(run)
    seed.commit()
    run_id = run.id
    seed.close()

    threads = 8
    rounds = 20
    barrier = threading.Barrier(threads)

    def worker() -> None:
        barrier.wait()
        for _ in range(rounds):
            db = factory()
            try:
                db.execute(
                    update(CommanderRun)
                    .where(CommanderRun.id == run_id)
                    .values(tokens_total=CommanderRun.tokens_total + 1)
                )
                service._safe_commit(db)
            finally:
                db.close()

    with ThreadPoolExecutor(max_workers=threads) as pool:
        futures = [pool.submit(worker) for _ in range(threads)]
        for future in futures:
            future.result()  # 任一 worker 抛异常都会让测试失败

    check = factory()
    try:
        value = check.scalar(select(CommanderRun.tokens_total).where(CommanderRun.id == run_id))
    finally:
        check.close()
        engine.dispose()
    assert value == threads * rounds
