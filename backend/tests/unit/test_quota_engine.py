"""分级额度引擎：层级匹配、Token 记账、三种超限动作、临时额度与过期。"""

import json
import math
from datetime import UTC, datetime, timedelta

import pytest
from app.core.errors import AppError
from app.modules.agents.models import YardAgent
from app.modules.quota import engine, service
from app.modules.quota.models import (
    STATUS_APPROVED,
    UNLIMITED_LIMIT,
    QuotaGrant,
    QuotaPolicy,
    QuotaRequest,
)

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


# ---------------------------------------------------------------------------
# 审查 B3：区分「无限制」与「零余额」，到期失败关闭而非无限免费
# ---------------------------------------------------------------------------


def test_zero_limit_is_zero_balance_not_unlimited(db_session):
    """limit_value=0 且无临时额度 = 零余额，必须拒绝（旧实现误当无限制放行）。"""
    policy = _policy(db_session, limit_value=0, on_exceed="block")
    assert engine.effective_limit(db_session, policy) == 0
    with pytest.raises(AppError) as exc:
        engine.preflight(db_session, YARD)
    assert exc.value.code == "QUOTA_EXCEEDED"
    assert exc.value.details["limit"] == 0
    assert exc.value.details["used"] == 0
    assert exc.value.details["requestable"] is False


def test_zero_limit_approve_opens_request(db_session):
    """零余额 + approve：拒绝并自动建提额申请（付费到期转申请，而非放行）。"""
    _policy(db_session, limit_value=0, on_exceed="approve")
    with pytest.raises(AppError) as exc:
        engine.preflight(db_session, YARD, requester_id="u-operator")
    assert exc.value.details["requestable"] is True
    assert exc.value.details["request_id"]
    pending = db_session.query(QuotaRequest).filter(QuotaRequest.status == "pending").count()
    assert pending == 1


def test_zero_limit_calls_denies_first_call(db_session):
    """次数指标零余额：前付费 0+1<=0 不成立，第一次调用即拒绝。"""
    _policy(db_session, metric="calls", limit_value=0, on_exceed="block")
    with pytest.raises(AppError):
        engine.preflight(db_session, YARD)


def test_unlimited_sentinel_always_allows(db_session):
    """limit_value<0 是显式无限制：无论用多少都放行，effective_limit=inf。"""
    policy = _policy(db_session, limit_value=UNLIMITED_LIMIT, on_exceed="block")
    assert engine.is_unlimited(policy)
    assert engine.effective_limit(db_session, policy) == math.inf
    engine.charge(db_session, YARD, tokens=10**12)
    db_session.commit()
    engine.preflight(db_session, YARD)  # 不抛错
    engine.preflight(db_session, YARD, now=datetime.now(UTC) + timedelta(days=400))


def test_grant_expiry_restores_zero_balance_denial(db_session):
    """B3 核心到期边界：billing 式 limit_value=0 基线 + 限时 grant。

    grant 有效期内放行；到期后 effective_limit 归零 → 拒绝服务，绝不变成
    无限免费用（旧实现到期后 limit<=0 被跳过 = 无限制，正是商用漏洞）。
    """
    policy = _policy(db_session, limit_value=0, on_exceed="block")
    expiry = datetime.now(UTC) + timedelta(days=30)
    db_session.add(QuotaGrant(yard_id=YARD, policy_id=policy.id, amount=1000, expires_at=expiry))
    db_session.commit()

    before = expiry - timedelta(seconds=1)
    assert engine.effective_limit(db_session, policy, before) == 1000
    engine.preflight(db_session, YARD, now=before)  # 有效期内放行

    # 到期当刻及之后：临时额度失效，回到零余额 → 拒绝
    for moment in (expiry, expiry + timedelta(seconds=1), expiry + timedelta(days=1)):
        assert engine.effective_limit(db_session, policy, moment) == 0
        with pytest.raises(AppError) as exc:
            engine.preflight(db_session, YARD, now=moment)
        assert exc.value.code == "QUOTA_EXCEEDED"
        assert exc.value.details["limit"] == 0


def test_grant_expiry_boundary_is_strictly_greater(db_session):
    """到期判定用 expires_at > now：等于到期时刻即失效（边界不宽限）。"""
    policy = _policy(db_session, limit_value=0, on_exceed="block")
    expiry = datetime.now(UTC).replace(microsecond=0) + timedelta(days=7)
    db_session.add(QuotaGrant(yard_id=YARD, policy_id=policy.id, amount=500, expires_at=expiry))
    db_session.commit()
    assert engine.effective_limit(db_session, policy, expiry - timedelta(microseconds=1)) == 500
    assert engine.effective_limit(db_session, policy, expiry) == 0


def test_unlimited_and_zero_payloads_are_json_safe(db_session):
    """无限制 payload 把 inf 转 null 并打 unlimited 标；零余额标 0，均可 JSON 序列化。"""
    unlimited = _policy(db_session, limit_value=UNLIMITED_LIMIT, metric="tokens", window="month")
    payload = service._policy_payload(db_session, unlimited)
    assert payload["unlimited"] is True
    assert payload["effective_limit"] is None
    assert payload["remaining"] is None
    assert payload["usage_percent"] == 0.0
    json.dumps(payload)  # 不抛 ValueError（inf 不可序列化）

    # 零余额策略：换一个窗口避免与上面唯一键冲突
    zero = _policy(db_session, limit_value=0, metric="tokens", window="day")
    zero_payload = service._policy_payload(db_session, zero)
    assert zero_payload["unlimited"] is False
    assert zero_payload["effective_limit"] == 0
    assert zero_payload["remaining"] == 0
    json.dumps(zero_payload)
