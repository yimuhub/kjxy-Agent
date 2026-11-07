"""工单逾期升级 inline 调度测试（v3.71）：单轮执行复用注入会话且异常兜底。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.modules.farm import worker_tasks
from app.modules.farm.models import WorkOrder
from app.modules.yards.repository import create_yard


@pytest.fixture()
def world(db_session):
    yard = create_yard(db_session, name="升级小院")
    return {"yard": yard, "db": db_session}


def test_run_inline_escalate_once_uses_injected_session(world):
    db = world["db"]
    overdue = WorkOrder(
        yard_id=world["yard"].id,
        title="逾期工单",
        status="open",
        priority="normal",
        due_on=(datetime.now(UTC) - timedelta(days=2)).date(),
    )
    healthy = WorkOrder(
        yard_id=world["yard"].id,
        title="未到期工单",
        status="open",
        priority="normal",
        due_on=(datetime.now(UTC) + timedelta(days=2)).date(),
    )
    db.add_all([overdue, healthy])
    db.commit()

    stats = worker_tasks.run_inline_escalate_once(db)
    assert stats["scanned"] >= 1
    assert stats["escalated"] == 1
    db.refresh(overdue)
    assert overdue.priority == "high"
    assert overdue.escalation_level == 1
    db.refresh(healthy)
    assert healthy.priority == "normal"


def test_run_inline_escalate_once_swallows_errors(monkeypatch):
    from app.modules.farm import workorder_service

    def boom(db, **kwargs):
        raise RuntimeError("db gone")

    monkeypatch.setattr(workorder_service, "escalate_overdue", boom)
    # 不注入会话：函数内部自开 SessionLocal，异常被兜住不外抛
    stats = worker_tasks.run_inline_escalate_once(None)
    assert stats == {"scanned": 0, "escalated": 0}
