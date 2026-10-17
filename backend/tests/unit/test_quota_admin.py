"""配额运营化测试：PATCH /admin/quotas/{tenant_id}、GET period 标注、阈值告警。

API 测试复用 conftest 的 client（内存库 + get_db_session 覆盖，全量 v1 路由已挂载）。
"""

import re

import pytest
from app.core.security import create_access_token
from app.modules.audit.models import AuditLog
from app.modules.tenants import quota as quota_mod
from app.modules.tenants.repository import create_tenant


def _hdr(user_id: str = "u-quota-admin", tenant_id: str | None = None, roles=("tenant_admin",)):
    token = create_access_token(user_id=user_id, tenant_id=tenant_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def tenant(db_session):
    t = create_tenant(db_session, name="配额测试租户")
    db_session.commit()
    return t


def test_patch_quota_forbidden_without_permission(client, tenant):
    resp = client.patch(
        f"/api/v1/admin/quotas/{tenant.id}",
        json={"tasks_limit": 10},
        headers=_hdr(tenant_id=tenant.id, roles=("viewer",)),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_patch_quota_ok_and_audit(client, db_session, tenant):
    resp = client.patch(
        f"/api/v1/admin/quotas/{tenant.id}",
        json={"tasks_limit": 7, "llm_calls_limit": 0},
        headers=_hdr(tenant_id=tenant.id),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["quotas"]["tasks"]["limit"] == 7
    assert data["quotas"]["llm_calls"]["limit"] == 0
    assert data["quotas"]["knowledge_docs"]["limit"] == quota_mod.DEFAULT_LIMITS["knowledge_docs"]
    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "quota_admin_patch", AuditLog.tenant_id == tenant.id)
        .count()
    )
    assert audit == 1


def test_patch_quota_rejects_negative_and_empty(client, tenant):
    bad = client.patch(
        f"/api/v1/admin/quotas/{tenant.id}",
        json={"tasks_limit": -1},
        headers=_hdr(tenant_id=tenant.id),
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "INVALID_REQUEST"

    empty = client.patch(
        f"/api/v1/admin/quotas/{tenant.id}",
        json={},
        headers=_hdr(tenant_id=tenant.id),
    )
    assert empty.status_code == 400
    assert empty.json()["error"]["code"] == "INVALID_REQUEST"


def test_patch_quota_unknown_tenant_404(client):
    resp = client.patch(
        "/api/v1/admin/quotas/no-such-tenant",
        json={"tasks_limit": 1},
        headers=_hdr(tenant_id="any"),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "TENANT_NOT_FOUND"


def test_get_quota_contains_period_label(client, tenant):
    resp = client.get("/api/v1/tenants/quota", headers=_hdr(tenant_id=tenant.id))
    assert resp.status_code == 200
    period = resp.json()["data"]["period"]
    assert re.fullmatch(r"\d{4}-\d{2}", period)


def test_alerts_at_90_and_100_percent(db_session, monkeypatch):
    """check_and_consume 跨 90%/100% 各发一条告警，dedupe_key 含阈值档位（§16.2）。"""
    calls: list[dict] = []
    monkeypatch.setattr(
        quota_mod, "send_alert", lambda title, message, **kw: calls.append({"title": title, **kw})
    )

    tenant_id = "t-alert"
    q = quota_mod.ensure_quota(db_session, tenant_id)
    q.tasks_limit = 10
    db_session.commit()

    for _ in range(8):  # 80%：不告警
        quota_mod.check_and_consume(db_session, tenant_id, "tasks")
    assert calls == []

    quota_mod.check_and_consume(db_session, tenant_id, "tasks")  # 9/10 = 90%
    assert len(calls) == 1
    assert calls[0]["title"] == "quota"
    assert calls[0]["dedupe_key"] == f"quota:{tenant_id}:tasks:90"

    quota_mod.check_and_consume(db_session, tenant_id, "tasks")  # 10/10 = 100%
    assert len(calls) == 2
    assert calls[1]["dedupe_key"] == f"quota:{tenant_id}:tasks:100"

    with pytest.raises(Exception) as exc:
        quota_mod.check_and_consume(db_session, tenant_id, "tasks")  # 超限时拒绝
    assert exc.value.code == "QUOTA_EXCEEDED"
