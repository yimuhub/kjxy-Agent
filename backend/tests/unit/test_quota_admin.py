"""配额运营化测试：PATCH /admin/quotas/{yard_id}、POST reset、GET period 标注、阈值告警。

API 测试复用 conftest 的 client（内存库 + get_db_session 覆盖，全量 v1 路由已挂载）。
"""

import re

import pytest
from app.core.security import create_access_token
from app.modules.audit.models import AuditLog
from app.modules.yards import quota as quota_mod
from app.modules.yards.models import YardMembership
from app.modules.yards.repository import create_yard

ADMIN_ID = "u-quota-admin"


def _hdr(user_id: str = ADMIN_ID, yard_id: str | None = None, roles=("yard_admin",)):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def yard(db_session):
    t = create_yard(db_session, name="配额测试小院")
    db_session.add(YardMembership(yard_id=t.id, user_id=ADMIN_ID, role="yard_admin"))
    db_session.commit()
    return t


def _viewer(db_session, yard_id: str) -> str:
    """建一个 viewer 成员：DB 角色优先，JWT 里的角色不会提权。"""
    db_session.add(
        YardMembership(yard_id=yard_id, user_id="u-quota-viewer", role="viewer", status="active")
    )
    db_session.commit()
    return "u-quota-viewer"


def test_patch_quota_forbidden_without_permission(client, db_session, yard):
    viewer = _viewer(db_session, yard.id)
    resp = client.patch(
        f"/api/v1/admin/quotas/{yard.id}",
        json={"tasks_limit": 10},
        headers=_hdr(user_id=viewer, yard_id=yard.id, roles=("viewer",)),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_patch_quota_forbidden_across_yards(client, db_session, yard):
    """A 院管理员不能改 B 院额度：JWT 角色兜底不能替代成员身份。"""
    other = create_yard(db_session, name="另一个小院")
    db_session.commit()
    resp = client.patch(
        f"/api/v1/admin/quotas/{other.id}",
        json={"tasks_limit": 999_999},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_patch_quota_ok_and_audit(client, db_session, yard):
    resp = client.patch(
        f"/api/v1/admin/quotas/{yard.id}",
        json={"tasks_limit": 7, "llm_calls_limit": 0},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["quotas"]["tasks"]["limit"] == 7
    assert data["quotas"]["llm_calls"]["limit"] == 0
    assert data["quotas"]["knowledge_docs"]["limit"] == quota_mod.DEFAULT_LIMITS["knowledge_docs"]
    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "quota_admin_patch", AuditLog.yard_id == yard.id)
        .count()
    )
    assert audit == 1


def test_patch_quota_rejects_negative_and_empty(client, yard):
    bad = client.patch(
        f"/api/v1/admin/quotas/{yard.id}",
        json={"tasks_limit": -1},
        headers=_hdr(yard_id=yard.id),
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "INVALID_REQUEST"

    empty = client.patch(
        f"/api/v1/admin/quotas/{yard.id}",
        json={},
        headers=_hdr(yard_id=yard.id),
    )
    assert empty.status_code == 400
    assert empty.json()["error"]["code"] == "INVALID_REQUEST"


def test_patch_quota_unknown_yard_404(client):
    resp = client.patch(
        "/api/v1/admin/quotas/no-such-yard",
        json={"tasks_limit": 1},
        headers=_hdr(yard_id="any"),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "YARD_NOT_FOUND"


def test_reset_usage_clears_used_keeps_limit(client, db_session, yard):
    q = quota_mod.ensure_quota(db_session, yard.id)
    q.llm_calls_limit = 20
    q.llm_calls_used = 20
    db_session.commit()

    resp = client.post(
        f"/api/v1/admin/quotas/{yard.id}/reset",
        json={"kinds": ["llm_calls"]},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["data"]["quotas"]["llm_calls"]
    assert item["used"] == 0
    assert item["limit"] == 20
    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "quota_admin_reset", AuditLog.yard_id == yard.id)
        .count()
    )
    assert audit == 1


def test_reset_usage_all_and_unknown_kind(client, db_session, yard):
    q = quota_mod.ensure_quota(db_session, yard.id)
    q.llm_calls_used = 3
    q.tasks_used = 4
    db_session.commit()

    bad = client.post(
        f"/api/v1/admin/quotas/{yard.id}/reset",
        json={"kinds": ["nope"]},
        headers=_hdr(yard_id=yard.id),
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "INVALID_REQUEST"

    resp = client.post(
        f"/api/v1/admin/quotas/{yard.id}/reset",
        json={},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200
    quotas = resp.json()["data"]["quotas"]
    assert all(item["used"] == 0 for item in quotas.values())


def test_reset_forbidden_for_viewer(client, db_session, yard):
    viewer = _viewer(db_session, yard.id)
    resp = client.post(
        f"/api/v1/admin/quotas/{yard.id}/reset",
        json={},
        headers=_hdr(user_id=viewer, yard_id=yard.id, roles=("viewer",)),
    )
    assert resp.status_code == 403


def test_get_quota_contains_period_and_label(client, yard):
    resp = client.get("/api/v1/yards/quota", headers=_hdr(yard_id=yard.id))
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert re.fullmatch(r"\d{4}-\d{2}", data["period"])
    assert data["quotas"]["llm_calls"]["label"] == "模型调用"


def test_alerts_at_90_and_100_percent(db_session, monkeypatch):
    """check_and_consume 跨 90%/100% 各发一条告警，dedupe_key 含阈值档位（§16.2）。"""
    calls: list[dict] = []
    monkeypatch.setattr(
        quota_mod, "send_alert", lambda title, message, **kw: calls.append({"title": title, **kw})
    )

    yard_id = "t-alert"
    q = quota_mod.ensure_quota(db_session, yard_id)
    q.tasks_limit = 10
    db_session.commit()

    for _ in range(8):  # 80%：不告警
        quota_mod.check_and_consume(db_session, yard_id, "tasks")
    assert calls == []

    quota_mod.check_and_consume(db_session, yard_id, "tasks")  # 9/10 = 90%
    assert len(calls) == 1
    assert calls[0]["title"] == "quota"
    assert calls[0]["dedupe_key"] == f"quota:{yard_id}:tasks:90"

    quota_mod.check_and_consume(db_session, yard_id, "tasks")  # 10/10 = 100%
    assert len(calls) == 2
    assert calls[1]["dedupe_key"] == f"quota:{yard_id}:tasks:100"

    with pytest.raises(Exception) as exc:
        quota_mod.check_and_consume(db_session, yard_id, "tasks")  # 超限时拒绝
    assert exc.value.code == "QUOTA_EXCEEDED"
