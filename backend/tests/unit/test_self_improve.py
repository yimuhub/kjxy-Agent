"""Self-improve service unit tests (v3.4): schedule, sync, execution edge cases."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from app.core.errors import AppError
from app.modules.auth.repository import create_user
from app.modules.llm.schemas import LLMResponse
from app.modules.self_improve import service as si_service
from app.modules.self_improve.models import (
    JOB_SELF_IMPROVE,
    JOB_STATUS_ACTIVE,
    JOB_STATUS_PAUSED,
    RUN_FAILED,
    RUN_SUCCESS,
    SelfImproveMemory,
    YardJob,
    YardJobRun,
)
from app.modules.yards.repository import add_membership, create_yard
from sqlalchemy import select

_BASE_SETTING = {
    "enabled": True,
    "schedule": {"preset": "daily", "custom_value": 1, "custom_unit": "days"},
    "scope": "per_member",
    "daily_budget_tokens": 200000,
    "auto_publish": False,
    "retain_days": 90,
    "notify_owner": True,
}


# ---------------------------------------------------------------------------
# schedule_interval
# ---------------------------------------------------------------------------


class TestScheduleInterval:
    def test_preset_daily(self):
        assert si_service.schedule_interval(_BASE_SETTING) == timedelta(days=1)

    def test_preset_weekly(self):
        setting = {**_BASE_SETTING, "schedule": {"preset": "weekly"}}
        assert si_service.schedule_interval(setting) == timedelta(days=7)

    def test_custom_hours(self):
        setting = {
            **_BASE_SETTING,
            "schedule": {"preset": "custom", "custom_value": 6, "custom_unit": "hours"},
        }
        assert si_service.schedule_interval(setting) == timedelta(hours=6)

    def test_custom_months(self):
        setting = {
            **_BASE_SETTING,
            "schedule": {"preset": "custom", "custom_value": 2, "custom_unit": "months"},
        }
        assert si_service.schedule_interval(setting) == timedelta(days=60)

    def test_custom_invalid_unit_falls_back_to_days(self):
        setting = {
            **_BASE_SETTING,
            "schedule": {"preset": "custom", "custom_value": 3, "custom_unit": "foo"},
        }
        assert si_service.schedule_interval(setting) == timedelta(days=3)

    def test_custom_value_clamped_min(self):
        setting = {
            **_BASE_SETTING,
            "schedule": {"preset": "custom", "custom_value": 0, "custom_unit": "days"},
        }
        assert si_service.schedule_interval(setting) == timedelta(days=1)

    def test_custom_value_clamped_max(self):
        setting = {
            **_BASE_SETTING,
            "schedule": {"preset": "custom", "custom_value": 9999, "custom_unit": "hours"},
        }
        assert si_service.schedule_interval(setting) == timedelta(hours=720)

    def test_unknown_preset_falls_back_daily(self):
        setting = {**_BASE_SETTING, "schedule": {"preset": "quadratic_weekly"}}
        assert si_service.schedule_interval(setting) == timedelta(days=1)

    def test_custom_non_int_value_falls_back_daily(self):
        setting = {
            **_BASE_SETTING,
            "schedule": {"preset": "custom", "custom_value": "abc", "custom_unit": "days"},
        }
        assert si_service.schedule_interval(setting) == timedelta(days=1)


# ---------------------------------------------------------------------------
# sync_job
# ---------------------------------------------------------------------------


class TestSyncJob:
    def test_creates_active_job_when_enabled(self, db_session):
        yard = create_yard(db_session, name="si-sync-test")
        owner = create_user(db_session, username="si_owner", password="Passw0rd123")
        yard.owner_user_id = owner.id
        add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="yard_owner")
        db_session.commit()

        job = si_service.sync_job(
            db_session, yard_id=yard.id, setting=_BASE_SETTING, actor_id=owner.id
        )
        db_session.commit()
        assert job.status == JOB_STATUS_ACTIVE
        assert job.next_run_at is not None
        assert job.kind == JOB_SELF_IMPROVE

    def test_creates_paused_job_when_disabled(self, db_session):
        yard = create_yard(db_session, name="si-paused-test")
        owner = create_user(db_session, username="si_owner2", password="Passw0rd123")
        yard.owner_user_id = owner.id
        add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="yard_owner")
        db_session.commit()

        setting = {**_BASE_SETTING, "enabled": False}
        job = si_service.sync_job(db_session, yard_id=yard.id, setting=setting, actor_id=owner.id)
        db_session.commit()
        assert job.status == JOB_STATUS_PAUSED
        assert job.next_run_at is None

    def test_update_preserves_schedule_when_not_changed(self, db_session):
        yard = create_yard(db_session, name="si-update-test")
        owner = create_user(db_session, username="si_owner3", password="Passw0rd123")
        yard.owner_user_id = owner.id
        add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="yard_owner")
        db_session.commit()

        job1 = si_service.sync_job(
            db_session, yard_id=yard.id, setting=_BASE_SETTING, actor_id=owner.id
        )
        original_next = job1.next_run_at
        db_session.commit()

        job2 = si_service.sync_job(
            db_session, yard_id=yard.id, setting=_BASE_SETTING, actor_id=owner.id
        )
        # SQLite strips tzinfo on roundtrip; compare naive representations
        orig_naive = original_next.replace(tzinfo=None) if original_next.tzinfo else original_next
        new_naive = (
            job2.next_run_at.replace(tzinfo=None) if job2.next_run_at.tzinfo else job2.next_run_at
        )
        assert new_naive == orig_naive  # not re-armed (interval unchanged)

    def test_disable_clears_next_run(self, db_session):
        yard = create_yard(db_session, name="si-disable-test")
        owner = create_user(db_session, username="si_owner4", password="Passw0rd123")
        yard.owner_user_id = owner.id
        add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="yard_owner")
        db_session.commit()

        si_service.sync_job(db_session, yard_id=yard.id, setting=_BASE_SETTING, actor_id=owner.id)
        db_session.commit()

        disabled = {**_BASE_SETTING, "enabled": False}
        job = si_service.sync_job(db_session, yard_id=yard.id, setting=disabled, actor_id=owner.id)
        db_session.commit()
        assert job.status == JOB_STATUS_PAUSED
        assert job.next_run_at is None
        assert job.lease_until is None


# ---------------------------------------------------------------------------
# run_once
# ---------------------------------------------------------------------------


def _make_yard_with_settings(db_session, setting_override: dict | None = None):
    yard = create_yard(db_session, name="si-run-test")
    owner = create_user(db_session, username="si_run_owner", password="Passw0rd123")
    yard.owner_user_id = owner.id
    add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="yard_owner")
    db_session.commit()

    from app.modules.yards.settings_service import update_setting

    setting = {**_BASE_SETTING, **(setting_override or {})}
    update_setting(
        db_session,
        yard_id=yard.id,
        key="self_improve",
        value=setting,
        actor_id=owner.id,
        request_id="test-setup",
    )
    db_session.commit()
    return yard, owner


def _mock_llm_success(payload: dict | None = None):
    """Return an AsyncMock that returns a successful LLMResponse."""
    if payload is None:
        payload = {
            "preferences": [{"user": "", "summary": "偏好中文沟通", "tags": ["中文"]}],
            "skill_proposals": [],
            "knowledge_candidates": [],
            "report": "本轮无特别产出",
        }
    resp = LLMResponse(
        content=json.dumps(payload, ensure_ascii=False),
        input_tokens=100,
        output_tokens=200,
        model="fake",
        latency_ms=50,
    )
    return AsyncMock(return_value=resp)


def _mock_llm_failure():
    async def _raise(*args, **kwargs):
        raise AppError("LLM_UNAVAILABLE", "模型不可用", http_status=503)

    return AsyncMock(side_effect=_raise)


class TestRunOnce:
    def test_disabled_raises_409(self, db_session):
        yard, owner = _make_yard_with_settings(db_session, {"enabled": False})
        with pytest.raises(AppError) as exc_info:
            si_service.run_once(
                db_session, yard_id=yard.id, actor_id=owner.id, request_id="test-req"
            )
        assert exc_info.value.code == "SELF_IMPROVE_DISABLED"

    def test_empty_window_success_no_llm(self, db_session):
        """No sessions/messages in window → success without LLM call."""
        yard, owner = _make_yard_with_settings(db_session)
        mock_llm = _mock_llm_success()
        with patch.object(si_service, "complete_with_logging", mock_llm):
            run = si_service.run_once(
                db_session, yard_id=yard.id, actor_id=owner.id, request_id="test-req"
            )
        assert run.status == RUN_SUCCESS
        assert run.output_json.get("note", "").startswith("窗口内无可用会话数据")
        mock_llm.assert_not_called()

    def test_budget_exhausted_raises_409(self, db_session):
        yard, owner = _make_yard_with_settings(db_session, {"daily_budget_tokens": 1000})
        # Create the job first (sync_job is called inside _make_yard_with_settings via
        # update_setting → settings_router path doesn't apply in unit test, so do it here)
        job = si_service.sync_job(
            db_session,
            yard_id=yard.id,
            setting={**_BASE_SETTING, "daily_budget_tokens": 1000},
            actor_id=owner.id,
        )
        db_session.commit()
        # Pre-fill a run that used 1000 tokens today
        db_session.add(
            YardJobRun(
                yard_id=yard.id,
                job_id=job.id,
                kind=JOB_SELF_IMPROVE,
                status=RUN_SUCCESS,
                trigger="manual",
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                tokens_used=1000,
            )
        )
        db_session.commit()
        with pytest.raises(AppError) as exc_info:
            si_service.run_once(
                db_session, yard_id=yard.id, actor_id=owner.id, request_id="test-req"
            )
        assert exc_info.value.code == "SELF_IMPROVE_BUDGET_EXHAUSTED"

    def test_llm_failure_produces_failed_run_no_outputs(self, db_session):
        yard, owner = _make_yard_with_settings(db_session)
        mock_fail = _mock_llm_failure()
        # Need "down" feedback in window so evidence["feedback"]["samples"] is non-empty
        from app.modules.evolution.models import Feedback

        db_session.add(
            Feedback(
                yard_id=yard.id,
                actor_id=owner.id,
                rating="down",
                feedback_text="测试反馈",
            )
        )
        db_session.commit()

        with patch.object(si_service, "complete_with_logging", mock_fail):
            run = si_service.run_once(
                db_session, yard_id=yard.id, actor_id=owner.id, request_id="test-req"
            )
        assert run.status == RUN_FAILED
        assert "llm_unavailable" in (run.error_text or "")
        # No outputs persisted
        mem = db_session.scalar(
            select(SelfImproveMemory).where(SelfImproveMemory.yard_id == yard.id)
        )
        assert mem is None

    def test_parse_failure_produces_failed_run(self, db_session):
        yard, owner = _make_yard_with_settings(db_session)
        # LLM returns garbage that is not valid JSON
        bad_resp = LLMResponse(
            content="这不是JSON输出",
            input_tokens=50,
            output_tokens=50,
            model="fake",
            latency_ms=10,
        )
        mock_bad = AsyncMock(return_value=bad_resp)

        from app.modules.evolution.models import Feedback

        db_session.add(
            Feedback(
                yard_id=yard.id,
                actor_id=owner.id,
                rating="down",
                feedback_text="另一个反馈",
            )
        )
        db_session.commit()

        with patch.object(si_service, "complete_with_logging", mock_bad):
            run = si_service.run_once(
                db_session, yard_id=yard.id, actor_id=owner.id, request_id="test-req"
            )
        assert run.status == RUN_FAILED
        assert "parse_failed" in (run.error_text or "")


# ---------------------------------------------------------------------------
# run_due_jobs (lease & not-due)
# ---------------------------------------------------------------------------


class TestRunDueJobs:
    def _get_job(self, db_session, yard):
        """sync_job creates the YardJob; we call it explicitly here."""
        job = db_session.scalar(
            select(YardJob).where(YardJob.yard_id == yard.id, YardJob.kind == JOB_SELF_IMPROVE)
        )
        if job is None:
            job = si_service.sync_job(
                db_session, yard_id=yard.id, setting=_BASE_SETTING, actor_id="test"
            )
            db_session.commit()
        return job

    def test_not_due_job_skipped(self, db_session):
        yard, owner = _make_yard_with_settings(db_session)
        job = self._get_job(db_session, yard)
        # Set next_run_at to tomorrow
        job.next_run_at = datetime.now(UTC) + timedelta(days=1)
        db_session.commit()

        stats = si_service.run_due_jobs(db_session, now=datetime.now(UTC))
        assert stats["due"] == 0

    def test_lease_held_skips_execution(self, db_session):
        yard, owner = _make_yard_with_settings(db_session)
        job = self._get_job(db_session, yard)
        now = datetime.now(UTC)
        job.next_run_at = now - timedelta(minutes=1)  # due
        job.lease_until = now + timedelta(seconds=60)  # lease active
        db_session.commit()

        stats = si_service.run_due_jobs(db_session, now=now)
        assert stats["due"] == 0  # skipped due to lease

    def test_successful_due_execution(self, db_session):
        yard, owner = _make_yard_with_settings(db_session)
        job = self._get_job(db_session, yard)
        now = datetime.now(UTC)
        job.next_run_at = now - timedelta(minutes=1)
        job.lease_until = None
        db_session.commit()

        mock_llm = _mock_llm_success()
        with patch.object(si_service, "complete_with_logging", mock_llm):
            stats = si_service.run_due_jobs(db_session, now=now)
        assert stats["due"] == 1
        assert stats["success"] == 1
        # next_run_at re-armed (SQLite may strip tzinfo; compare naive)
        db_session.refresh(job)
        assert job.next_run_at is not None
        next_naive = job.next_run_at.replace(tzinfo=None)
        now_naive = now.replace(tzinfo=None)
        assert next_naive > now_naive
