"""分级额度接口：策略读写、环境模板、申请与审批、权限边界。"""

import pytest
from app.core.security import create_access_token
from app.modules.agents.models import YardAgent
from app.modules.audit.models import AuditLog
from app.modules.quota.models import QuotaPolicy
from app.modules.yards.models import YardMembership
from app.modules.yards.repository import create_yard

ADMIN = "u-quota-mgr"


def _hdr(user_id: str, yard_id: str | None, roles=("yard_admin",)):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def yard(db_session):
    row = create_yard(db_session, name="额度接口小院")
    db_session.add_all(
        [
            YardMembership(yard_id=row.id, user_id=ADMIN, role="yard_admin", status="active"),
            YardMembership(yard_id=row.id, user_id="u-op", role="operator", status="active"),
            YardMembership(yard_id=row.id, user_id="u-view", role="viewer", status="active"),
            YardMembership(yard_id=row.id, user_id="u-exp", role="expert", status="active"),
        ]
    )
    db_session.add(
        YardAgent(
            yard_id=row.id,
            agent_code="gen_diag",
            name="诊断助手",
            persona_prompt="p",
            task_prompt_template="{{question}}",
            group_name="诊断",
            status="published",
        )
    )
    db_session.commit()
    return row


def test_overview_readable_by_expert(client, yard):
    resp = client.get("/api/v1/quota/overview", headers=_hdr("u-exp", yard.id, ("expert",)))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["environment"] == "standard"
    assert data["can_manage"] is False
    assert data["options"]["agents"][0]["code"] == "gen_diag"


def test_upsert_policy_requires_manage(client, db_session, yard):
    body = {
        "scope_type": "agent",
        "scope_key": "gen_diag",
        "metric": "tokens",
        "window": "day",
        "limit_value": 200000,
        "on_exceed": "approve",
    }
    forbidden = client.put(
        "/api/v1/quota/policies", json=body, headers=_hdr("u-op", yard.id, ("operator",))
    )
    assert forbidden.status_code == 403

    ok = client.put("/api/v1/quota/policies", json=body, headers=_hdr(ADMIN, yard.id))
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["limit_value"] == 200000
    assert ok.json()["data"]["scope_type_label"] == "单个 Agent"
    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "quota_policy_upsert", AuditLog.yard_id == yard.id)
        .count()
    )
    assert audit == 1


def test_upsert_rejects_empty_or_unknown_scope(client, yard):
    empty = client.put(
        "/api/v1/quota/policies",
        json={"scope_type": "model", "scope_key": "", "limit_value": 1},
        headers=_hdr(ADMIN, yard.id),
    )
    assert empty.status_code == 400

    missing = client.put(
        "/api/v1/quota/policies",
        json={"scope_type": "agent", "scope_key": "gen_nope", "limit_value": 1},
        headers=_hdr(ADMIN, yard.id),
    )
    assert missing.status_code == 404


def test_environment_switch_keeps_manual_policies(client, db_session, yard):
    client.put(
        "/api/v1/quota/policies",
        json={
            "scope_type": "yard",
            "metric": "cost",
            "window": "month",
            "limit_value": 50,
            "on_exceed": "warn",
        },
        headers=_hdr(ADMIN, yard.id),
    )
    first = client.put(
        "/api/v1/quota/environment",
        json={"environment": "trial"},
        headers=_hdr(ADMIN, yard.id),
    )
    assert first.status_code == 200, first.text
    quotas = {p["metric"]: p for p in first.json()["data"]["policies"]}
    assert quotas["tokens"]["limit_value"] == 500_000
    assert quotas["cost"]["limit_value"] == 50, "人工配置不能被模板覆盖"

    second = client.put(
        "/api/v1/quota/environment",
        json={"environment": "production"},
        headers=_hdr(ADMIN, yard.id),
    )
    assert second.status_code == 200
    quotas = {p["metric"]: p for p in second.json()["data"]["policies"]}
    assert quotas["tokens"]["limit_value"] == 10_000_000
    assert quotas["cost"]["limit_value"] == 50
    assert (
        db_session.query(QuotaPolicy)
        .filter(QuotaPolicy.yard_id == yard.id, QuotaPolicy.source == "template")
        .count()
        == 1
    )


def test_request_and_approve_flow(client, db_session, yard):
    created = client.put(
        "/api/v1/quota/policies",
        json={
            "scope_type": "yard",
            "metric": "tokens",
            "window": "month",
            "limit_value": 1000,
            "on_exceed": "approve",
        },
        headers=_hdr(ADMIN, yard.id),
    )
    policy_id = created.json()["data"]["id"]

    req = client.post(
        "/api/v1/quota/requests",
        json={
            "policy_id": policy_id,
            "requested_limit": 5000,
            "reason": "月底集中出报告",
            "valid_days": 3,
        },
        headers=_hdr("u-op", yard.id, ("operator",)),
    )
    assert req.status_code == 200, req.text
    request_id = req.json()["data"]["id"]
    assert req.json()["data"]["status"] == "pending"

    # 普通成员看不到别人的申请
    mine = client.get("/api/v1/quota/requests", headers=_hdr("u-view", yard.id, ("viewer",)))
    assert mine.json()["data"]["items"] == []

    approved = client.post(
        f"/api/v1/quota/requests/{request_id}/approve",
        json={"note": "同意", "approved_limit": 4000},
        headers=_hdr(ADMIN, yard.id),
    )
    assert approved.status_code == 200, approved.text
    data = approved.json()["data"]
    assert data["request"]["status"] == "approved"
    assert data["grant"]["amount"] == 3000


def test_cannot_approve_own_request(client, yard):
    created = client.put(
        "/api/v1/quota/policies",
        json={"scope_type": "yard", "limit_value": 100, "on_exceed": "approve"},
        headers=_hdr(ADMIN, yard.id),
    )
    policy_id = created.json()["data"]["id"]
    req = client.post(
        "/api/v1/quota/requests",
        json={"policy_id": policy_id, "requested_limit": 200},
        headers=_hdr(ADMIN, yard.id),
    )
    request_id = req.json()["data"]["id"]
    resp = client.post(
        f"/api/v1/quota/requests/{request_id}/approve",
        json={},
        headers=_hdr(ADMIN, yard.id),
    )
    assert resp.status_code == 403


def test_reset_policy_counter(client, yard):
    created = client.put(
        "/api/v1/quota/policies",
        json={"scope_type": "yard", "limit_value": 5, "on_exceed": "block"},
        headers=_hdr(ADMIN, yard.id),
    )
    policy_id = created.json()["data"]["id"]
    from app.modules.quota import engine

    resp = client.post(f"/api/v1/quota/policies/{policy_id}/reset", headers=_hdr(ADMIN, yard.id))
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["used"] == 0
    assert engine.window_start("month") == resp.json()["data"]["window_start"]


# ---------------------------------------------------------------------------
# 审查 B5：切换环境保留未到期购买权益（模板策略原地更新，grant 不孤儿）
# ---------------------------------------------------------------------------


def _tokens_policy(overview: dict) -> dict:
    return next(p for p in overview["policies"] if p["metric"] == "tokens")


def test_environment_switch_keeps_grant_policy_identity(client, db_session, yard):
    from datetime import UTC, datetime, timedelta

    from app.modules.quota import engine
    from app.modules.quota.models import QuotaGrant

    first = client.get("/api/v1/quota/overview", headers=_hdr(ADMIN, yard.id))
    assert first.status_code == 200, first.text
    policy = _tokens_policy(first.json()["data"])
    db_session.add(
        QuotaGrant(
            yard_id=yard.id,
            policy_id=policy["id"],
            amount=2_000_000,
            remaining=1_500_000,
            expires_at=datetime.now(UTC) + timedelta(days=20),
        )
    )
    db_session.commit()

    switched = client.put(
        "/api/v1/quota/environment",
        json={"environment": "trial"},
        headers=_hdr(ADMIN, yard.id),
    )
    assert switched.status_code == 200, switched.text
    tokens = _tokens_policy(switched.json()["data"])
    assert tokens["id"] == policy["id"], "模板策略原地更新，id 不变"
    assert tokens["limit_value"] == 500_000
    assert tokens["granted"] == 1_500_000, "未到期购买余额守恒"
    assert engine.grant_total(db_session, policy["id"]) == 1_500_000


def test_environment_switch_keeps_template_policy_with_balance(
    client, db_session, yard, monkeypatch
):
    """新环境不再包含某模板策略时，只要还有未到期余额就保留，不吞已购额度。"""
    from datetime import UTC, datetime, timedelta

    from app.modules.quota import engine, environments, service
    from app.modules.quota.models import QuotaGrant, QuotaPolicy

    first = client.get("/api/v1/quota/overview", headers=_hdr(ADMIN, yard.id))
    policy = _tokens_policy(first.json()["data"])
    db_session.add(
        QuotaGrant(
            yard_id=yard.id,
            policy_id=policy["id"],
            amount=1_000_000,
            remaining=800_000,
            expires_at=datetime.now(UTC) + timedelta(days=10),
        )
    )
    db_session.commit()

    monkeypatch.setitem(
        environments.ENVIRONMENTS,
        "lean",
        {"label": "精简", "description": "无模板策略", "policies": []},
    )
    # 直接走服务层：HTTP 契约的 environment 是 Literal，不允许新增未登记环境
    service.apply_environment(db_session, yard_id=yard.id, environment="lean", actor_id=ADMIN)
    kept = db_session.query(QuotaPolicy).filter(QuotaPolicy.id == policy["id"]).one_or_none()
    assert kept is not None, "带余额的旧模板策略不能被删除"
    assert engine.grant_total(db_session, policy["id"]) == 800_000


def test_delete_policy_blocked_while_grant_balance_remains(client, db_session, yard):
    from datetime import UTC, datetime, timedelta

    from app.modules.quota.models import QuotaGrant

    created = client.put(
        "/api/v1/quota/policies",
        json={"scope_type": "yard", "metric": "cost", "limit_value": 50, "on_exceed": "warn"},
        headers=_hdr(ADMIN, yard.id),
    )
    policy_id = created.json()["data"]["id"]
    grant = QuotaGrant(
        yard_id=yard.id,
        policy_id=policy_id,
        amount=100,
        remaining=100,
        expires_at=datetime.now(UTC) + timedelta(days=5),
    )
    db_session.add(grant)
    db_session.commit()

    blocked = client.delete(f"/api/v1/quota/policies/{policy_id}", headers=_hdr(ADMIN, yard.id))
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "QUOTA_POLICY_HAS_GRANTS"

    grant.remaining = 0  # 余额用尽后允许删除
    db_session.commit()
    ok = client.delete(f"/api/v1/quota/policies/{policy_id}", headers=_hdr(ADMIN, yard.id))
    assert ok.status_code == 200, ok.text
