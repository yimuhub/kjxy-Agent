"""Agent 自动执行调度与推送事件单测（v3.28）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.modules.farm import agent_jobs
from app.modules.push import service as push_service
from app.modules.push.models import EVENT_FARM_AGENT_RUN, EVENT_FARM_ALERT, VALID_EVENTS
from app.modules.self_improve.models import JOB_STATUS_ACTIVE, YardJob
from app.modules.yards.repository import create_yard
from app.modules.yards.settings_service import update_setting


def _enable(db, yard_id: str) -> None:
    update_setting(
        db,
        yard_id=yard_id,
        key="farm_agent",
        value={"enabled": True, "schedule": {"preset": "daily"}, "explain_with_llm": False},
        actor_id="u1",
        request_id="",
    )
    db.commit()


def test_sync_job_creates_and_pauses(db_session):
    yard = create_yard(db_session, name="调度小院")
    db_session.commit()
    _enable(db_session, yard.id)

    setting = {"enabled": True, "schedule": {"preset": "hours12"}}
    job = agent_jobs.sync_job(db_session, yard_id=yard.id, setting=setting, actor_id="u1")
    db_session.commit()
    assert job.kind == agent_jobs.JOB_KIND
    assert job.status == JOB_STATUS_ACTIVE
    assert job.next_run_at is not None

    paused = agent_jobs.sync_job(
        db_session,
        yard_id=yard.id,
        setting={"enabled": False, "schedule": {"preset": "daily"}},
        actor_id="u1",
    )
    db_session.commit()
    assert paused.next_run_at is None
    assert paused.status != JOB_STATUS_ACTIVE


def test_ensure_jobs_and_run_due(db_session):
    yard = create_yard(db_session, name="到期小院")
    db_session.commit()
    _enable(db_session, yard.id)

    assert agent_jobs.ensure_jobs(db_session) == 1
    db_session.commit()
    # 再跑一次不重复建
    assert agent_jobs.ensure_jobs(db_session) == 0

    job = db_session.query(YardJob).filter(YardJob.yard_id == yard.id).one()
    job.next_run_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.commit()

    stats = agent_jobs.run_due_automations(db_session, now=datetime.now(UTC))
    assert stats["due"] == 1
    assert stats["insufficient"] == 1  # 没有设备也没有天气 → 如实说证据不足

    db_session.refresh(job)
    assert job.last_run_at is not None
    assert job.next_run_at is not None


def test_push_events_cover_farm_agent():
    assert EVENT_FARM_ALERT in VALID_EVENTS
    assert EVENT_FARM_AGENT_RUN in VALID_EVENTS
    body = push_service.build_body(
        "generic_webhook", EVENT_FARM_ALERT, {"title": "墒情预警", "severity": "high"}
    )
    assert body["event"] == EVENT_FARM_ALERT
    assert body["payload"]["title"] == "墒情预警"
    markdown = push_service.build_body(
        "wecom_robot", EVENT_FARM_AGENT_RUN, {"title": "本轮 2 条发现", "pending": 1}
    )
    assert "Agent 自动执行" in markdown["markdown"]["content"]
