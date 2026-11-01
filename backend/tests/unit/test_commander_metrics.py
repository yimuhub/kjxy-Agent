"""指挥官指标聚合：空数据、状态分类、澄清排除、窗口与时长分位（离线）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.modules.agents.models import YardAgent
from app.modules.commander import metrics
from app.modules.commander.models import CommanderRun, CommanderSubtask
from app.modules.yards.repository import create_yard


@pytest.fixture()
def yard(db_session):
    row = create_yard(db_session, name="指挥官指标小院")
    db_session.commit()
    return row


def _run(
    db,
    yard,
    *,
    status: str,
    fallback: bool = False,
    error_code: str | None = None,
    effective_parallel: int = 3,
    degraded: bool = False,
    latency_ms: int = 0,
    created_at: datetime | None = None,
) -> CommanderRun:
    run = CommanderRun(
        yard_id=yard.id,
        session_id="s-metrics",
        user_id="u-metrics",
        goal="统计目标",
        plan_json={"fallback": fallback, "subtasks": []},
        status=status,
        max_parallel=3,
        effective_parallel=effective_parallel,
        degraded=degraded,
        tokens_total=0,
        latency_ms=latency_ms,
        error_code=error_code,
        roles_json=[],
        **({"created_at": created_at} if created_at is not None else {}),
    )
    db.add(run)
    db.flush()
    return run


def _subtask(db, yard, run, seq: int, status: str) -> None:
    db.add(
        CommanderSubtask(
            run_id=run.id,
            yard_id=yard.id,
            seq=seq,
            title=f"任务 {seq}",
            instruction="做事",
            expected_output="文本",
            agent_source="commander",
            agent_name="指挥官",
            status=status,
            depends_on_json=[],
            attempts=1,
        )
    )


def _agent(db, yard, *, code: str, name: str, created_from: str, created_at=None) -> None:
    db.add(
        YardAgent(
            yard_id=yard.id,
            agent_code=code,
            name=name,
            description="指标测试",
            emoji="🧩",
            persona_prompt="你是助手。",
            task_prompt_template="回答：{{question}}",
            allowed_tools=[],
            bound_skills=[],
            knowledge_scopes=[],
            status="draft",
            visibility="public",
            created_from=created_from,
            created_by="u-metrics",
            **({"created_at": created_at} if created_at is not None else {}),
        )
    )


def test_stats_empty_returns_zeroes(db_session, yard):
    result = metrics.stats(db_session, yard.id, days=30)
    assert result["runs"] == {
        "total": 0,
        "queued": 0,
        "running": 0,
        "completed": 0,
        "partial": 0,
        "failed": 0,
        "cancelled": 0,
    }
    assert result["plan"] == {"success_rate": 0.0, "fallback_rate": 0.0}
    assert result["subtasks"] == {
        "total": 0,
        "ok": 0,
        "error": 0,
        "skipped": 0,
        "success_rate": 0.0,
    }
    assert result["avg_effective_parallel"] == 0.0
    assert result["degraded_runs"] == 0
    assert result["auto_created_agents"] == 0
    assert result["latency_ms"] == {"p50": 0, "p90": 0, "max": 0}


def test_stats_aggregates_and_excludes_clarify_and_old_window(db_session, yard):
    completed = _run(db_session, yard, status="completed", latency_ms=100)
    _subtask(db_session, yard, completed, 1, "ok")
    _subtask(db_session, yard, completed, 2, "error")

    partial = _run(
        db_session,
        yard,
        status="partial",
        fallback=True,
        effective_parallel=2,
        degraded=True,
        latency_ms=300,
    )
    _subtask(db_session, yard, partial, 1, "ok")
    _subtask(db_session, yard, partial, 2, "skipped")

    failed = _run(db_session, yard, status="failed", effective_parallel=1, latency_ms=500)
    _subtask(db_session, yard, failed, 1, "error")

    cancelled = _run(db_session, yard, status="cancelled")
    _subtask(db_session, yard, cancelled, 1, "skipped")

    _run(db_session, yard, status="queued")

    clarify = _run(
        db_session,
        yard,
        status="cancelled",
        error_code=metrics.NEEDS_INPUT_CODE,
    )
    _subtask(db_session, yard, clarify, 1, "pending")

    old = datetime.now(UTC) - timedelta(days=40)
    _run(db_session, yard, status="completed", latency_ms=9999, created_at=old)

    _agent(db_session, yard, code="gen_a", name="AI 甲", created_from="commander")
    _agent(db_session, yard, code="gen_b", name="AI 乙", created_from="commander")
    _agent(db_session, yard, code="manual_c", name="手工丙", created_from="manual")
    _agent(
        db_session,
        yard,
        code="gen_old",
        name="过期 AI",
        created_from="commander",
        created_at=old,
    )
    db_session.commit()

    result = metrics.stats(db_session, yard.id, days=30)
    assert result["runs"] == {
        "total": 5,
        "queued": 1,
        "running": 0,
        "completed": 1,
        "partial": 1,
        "failed": 1,
        "cancelled": 1,
    }
    assert result["plan"] == {"success_rate": 0.8, "fallback_rate": 0.2}
    assert result["subtasks"] == {
        "total": 6,
        "ok": 2,
        "error": 2,
        "skipped": 2,
        "success_rate": 0.3333,
    }
    assert result["avg_effective_parallel"] == 2.0
    assert result["degraded_runs"] == 1
    assert result["auto_created_agents"] == 2
    assert result["latency_ms"] == {"p50": 300, "p90": 500, "max": 500}


def test_stats_clamps_window_days(db_session, yard):
    assert metrics.stats(db_session, yard.id, days=0)["window_days"] == 1
    assert metrics.stats(db_session, yard.id, days=999)["window_days"] == 365
