"""分级额度引擎：层级匹配、Token 记账、三种超限动作、临时额度与过期。"""

from datetime import UTC, datetime, timedelta

import pytest
from app.core.errors import AppError
from app.modules.agents.models import YardAgent
from app.modules.quota import engine
from app.modules.quota.models import STATUS_APPROVED, QuotaGrant, QuotaPolicy, QuotaRequest

YARD = "y-quota"


def _agent(db, code: str = "gen_diag", group: str | None = "诊断") -> YardAgent:
    row = YardAgent(
        yard_id=YARD,
        agent_code=code,
        name=f"助手-{code}",
        persona_prompt="p",
        task_prompt_template="{{question}}",
        group_name=group,
        status="published",
    )
    db.add(row)
    db.commit()
    return row


def _policy(db, **kw) -> QuotaPolicy:
    data = {
        "yard_id": YARD,
        "scope_type": "yard",
        "scope_key": "",
        "metric": "tokens",
        "window": "month",
        "limit_value": 100,
        "on_exceed": "block",
    }
    data.update(kw)
    row = QuotaPolicy(**data)
    db.add(row)
    db.commit()
    return row


def test_no_policy_is_noop(db_session):
    engine.preflight(db_session, YARD)
    engine.charge(db_session, YARD, tokens=1000)
    db_session.commit()


def test_tokens_exceed_blocks_next_call(db_session):
    _policy(db_session, limit_value=100, on_exceed="block")
    engine.charge(db_session, YARD, tokens=60)
    db_session.commit()
    engine.preflight(db_session, YARD)  # 未用尽，放行

    engine.charge(db_session, YARD, tokens=50)
    db_session.commit()
    with pytest.raises(AppError) as exc:
        engine.preflight(db_session, YARD)
    assert exc.value.code == "QUOTA_EXCEEDED"
    assert exc.value.details["used"] == 110
    assert exc.value.details["requestable"] is False


def test_warn_mode_never_raises(db_session, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        engine, "send_alert", lambda *a, **kw: calls.append(kw.get("dedupe_key", ""))
    )
    _policy(db_session, limit_value=10, on_exceed="warn")
    engine.charge(db_session, YARD, tokens=10)
    db_session.commit()
    engine.preflight(db_session, YARD)
    engine.preflight(db_session, YARD)
    assert any(str(k).endswith(":100") for k in calls)


def test_approve_mode_creates_request_and_grant_unblocks(db_session):
    policy = _policy(db_session, limit_value=100, on_exceed="approve")
    engine.charge(db_session, YARD, tokens=100)
    db_session.commit()

    with pytest.raises(AppError) as exc:
        engine.preflight(db_session, YARD, requester_id="u-operator")
    details = exc.value.details
    assert details["requestable"] is True
    request_id = details["request_id"]
    assert request_id

    # 重复预检不重复建单
    with pytest.raises(AppError):
        engine.preflight(db_session, YARD, requester_id="u-operator")
    pending = db_session.query(QuotaRequest).filter(QuotaRequest.status == "pending").count()
    assert pending == 1

    row = db_session.get(QuotaRequest, request_id)
    grant = engine.approve_request(db_session, row, decided_by="u-owner")
    assert grant.amount > 0
    assert row.status == STATUS_APPROVED
    assert engine.effective_limit(db_session, policy) == 100 + grant.amount
    engine.preflight(db_session, YARD)  # 提额后恢复


def test_expired_grant_is_ignored(db_session):
    policy = _policy(db_session, limit_value=100)
    db_session.add(
        QuotaGrant(
            yard_id=YARD,
            policy_id=policy.id,
            amount=500,
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    )
    db_session.commit()
    assert engine.effective_limit(db_session, policy) == 100


def test_agent_group_and_model_scopes(db_session):
    _agent(db_session, code="gen_diag", group="诊断")
    _policy(
        db_session,
        scope_type="agent_group",
        scope_key="诊断",
        limit_value=10,
        on_exceed="block",
    )
    _policy(
        db_session,
        scope_type="model",
        scope_key="big-model",
        limit_value=20,
        on_exceed="block",
    )

    engine.charge(db_session, YARD, agent_code="gen_diag", tokens=20)
    engine.charge(db_session, YARD, model="big-model", tokens=20)
    db_session.commit()

    with pytest.raises(AppError):
        engine.preflight(db_session, YARD, agent_code="gen_diag")
    with pytest.raises(AppError):
        engine.preflight(db_session, YARD, model="big-model")
    # 不匹配的 Agent / 模型不受影响
    engine.preflight(db_session, YARD, agent_code="gen_other", model="small-model")


def test_window_rollover_resets_usage(db_session):
    _policy(db_session, limit_value=10, window="day", on_exceed="block")
    engine.charge(db_session, YARD, tokens=10)
    db_session.commit()
    with pytest.raises(AppError):
        engine.preflight(db_session, YARD)
    tomorrow = datetime.now(UTC) + timedelta(days=1)
    engine.preflight(db_session, YARD, now=tomorrow)


def test_calls_metric_is_prepaid(db_session):
    _policy(db_session, metric="calls", limit_value=2, on_exceed="block")
    engine.charge(db_session, YARD, calls=1)
    db_session.commit()
    engine.preflight(db_session, YARD)  # 1 + 1 <= 2
    engine.charge(db_session, YARD, calls=1)
    db_session.commit()
    with pytest.raises(AppError):
        engine.preflight(db_session, YARD)  # 2 + 1 > 2
