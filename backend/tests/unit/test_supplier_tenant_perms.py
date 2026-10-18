"""M1（ADR-0005）：模型供应商管理权下放 tenant_admin。

覆盖：
- 角色+归属双校验：tenant_admin 对本租户行增/改/删/探测/启停 200；
  对平台全局行一律 404（越权与不存在统一，不泄露存在性）；跨租户 A 改 B 404；
- 平台全局行在租户视角列表 readonly=true、本租户行 readonly=false；平台管理员恒 false；
- set-active：资源篮本租户行与平台全局行均可；
- 请求体伪造 tenant_id 被 schema 拒绝（extra=forbid → 全局校验处理器 400 INVALID_REQUEST）；
- 写操作与 set-active 均落审计（action 前缀 supplier_，metadata 不含密钥）；被拒操作无审计；
- platform_admin 管理全局行回归不受影响。
"""

import os

os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-min-32-chars-for-unit-tests")
os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("EMBEDDING_PROVIDER", "mock")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("SUPPLIER_KEY_SECRET", Fernet.generate_key().decode())

import httpx  # noqa: E402
import pytest  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.modules.audit.models import AuditLog  # noqa: E402
from app.modules.llm.supplier_models import Supplier  # noqa: E402,F401 触发建表

TEST_KEY = "sk-perm-test-key-98765"


def _hdr(user_id: str, tenant_id: str | None = None, roles=("tenant_admin",)):
    token = create_access_token(user_id=user_id, tenant_id=tenant_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


def _create(client, hdr, name="deepseek-main", **extra):
    body = {
        "name": name,
        "provider_type": "deepseek",
        "base_url": "https://api.deepseek.com",
        "model_id": "deepseek-chat",
        "api_key": TEST_KEY,
    }
    body.update(extra)
    r = client.post("/api/v1/llm/providers", json=body, headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _audits(db_session, tenant_id, action):
    return (
        db_session.query(AuditLog)
        .filter(AuditLog.tenant_id == tenant_id, AuditLog.action == action)
        .all()
    )


class _FakeResp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        return _FakeResp(200, {"data": [{"id": "deepseek-chat"}]})

    async def post(self, url, json=None, headers=None):
        return _FakeResp(200, {"choices": [{"message": {"content": "hi"}}]})


@pytest.fixture()
def fake_httpx(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)


# ---------- 本租户行：增/改/删/探测/启停 全通 ----------


def test_tenant_admin_crud_own_supplier_ok(client, db_session):
    hdr = _hdr("u-ta", tenant_id="t-a")
    data = _create(client, hdr)
    sid = data["id"]
    assert data["tenant_id"] == "t-a"
    assert data["readonly"] is False  # 本租户行可编辑

    r = client.put(
        f"/api/v1/llm/providers/{sid}", json={"name": "renamed", "priority": 3}, headers=hdr
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["name"] == "renamed"

    r = client.post(f"/api/v1/llm/providers/{sid}/disable", headers=hdr)
    assert r.status_code == 200 and r.json()["data"]["status"] == 2
    r = client.post(f"/api/v1/llm/providers/{sid}/enable", headers=hdr)
    assert r.status_code == 200 and r.json()["data"]["status"] == 1

    r = client.delete(f"/api/v1/llm/providers/{sid}", headers=hdr)
    assert r.status_code == 200
    assert db_session.query(Supplier).count() == 0


def test_write_ops_and_set_active_write_audit_without_secret(client, db_session):
    hdr = _hdr("u-ta", tenant_id="t-a")
    data = _create(client, hdr)
    sid = data["id"]
    assert (
        client.put(f"/api/v1/llm/providers/{sid}", json={"priority": 9}, headers=hdr).status_code
        == 200
    )
    assert client.post(f"/api/v1/llm/providers/{sid}/disable", headers=hdr).status_code == 200
    assert client.post(f"/api/v1/llm/providers/{sid}/enable", headers=hdr).status_code == 200
    assert (
        client.post(
            "/api/v1/llm/providers/active", json={"supplier_id": sid}, headers=hdr
        ).status_code
        == 200
    )
    assert client.delete(f"/api/v1/llm/providers/{sid}", headers=hdr).status_code == 200

    for action in (
        "supplier_create",
        "supplier_update",
        "supplier_disable",
        "supplier_enable",
        "supplier_set_active",
        "supplier_delete",
    ):
        rows = _audits(db_session, "t-a", action)
        assert len(rows) == 1, action
        meta = rows[0].metadata_json or {}
        # metadata 只放 supplier_id/name/provider_type，绝不含密钥材料
        assert set(meta.keys()) <= {"supplier_id", "name", "provider_type"}
        assert TEST_KEY not in str(meta)
        assert TEST_KEY not in str(rows[0].__dict__)
    assert _audits(db_session, "t-a", "supplier_set_active")[0].resource_id == sid


def test_probe_saved_own_supplier_ok_and_audited(client, db_session, fake_httpx):
    hdr = _hdr("u-ta", tenant_id="t-a")
    sid = _create(client, hdr)["id"]
    r = client.post(f"/api/v1/llm/providers/{sid}/test", headers=hdr)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["result"]["ok"] is True
    assert len(_audits(db_session, "t-a", "supplier_test")) == 1


# ---------- 平台全局行：租户视角只读（写一律 404），可选为生效 ----------


@pytest.fixture()
def global_sid(client):
    """平台管理员建全局行。"""
    data = _create(
        client, _hdr("u-pa", roles=("platform_admin",)), name="global-shared", scope="global"
    )
    assert data["tenant_id"] is None
    return data["id"]


def test_tenant_admin_cannot_modify_global_supplier(client, db_session, global_sid):
    hdr = _hdr("u-ta", tenant_id="t-a")
    r = client.put(f"/api/v1/llm/providers/{global_sid}", json={"name": "hijack"}, headers=hdr)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "SUPPLIER_NOT_FOUND"
    assert client.delete(f"/api/v1/llm/providers/{global_sid}", headers=hdr).status_code == 404
    assert (
        client.post(f"/api/v1/llm/providers/{global_sid}/disable", headers=hdr).status_code == 404
    )
    # 探测会写回供应商行 → 同样视为写，404
    assert client.post(f"/api/v1/llm/providers/{global_sid}/test", headers=hdr).status_code == 404
    # 被拒操作不得留下成功审计
    assert _audits(db_session, "t-a", "supplier_update") == []
    assert _audits(db_session, "t-a", "supplier_delete") == []


def test_tenant_admin_set_active_global_allowed(client, global_sid):
    hdr = _hdr("u-ta", tenant_id="t-a")
    r = client.post("/api/v1/llm/providers/active", json={"supplier_id": global_sid}, headers=hdr)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["is_active"] is True


def test_list_readonly_flags(client):
    ta = _hdr("u-ta", tenant_id="t-a")
    _create(client, ta, name="own")
    _create(client, _hdr("u-pa", roles=("platform_admin",)), name="global", scope="global")

    items = client.get("/api/v1/llm/providers", headers=ta).json()["data"]
    by_name = {i["name"]: i for i in items}
    assert by_name["own"]["readonly"] is False
    assert by_name["global"]["readonly"] is True  # 前端据此禁用编辑按钮

    pa = _hdr("u-pa", tenant_id="t-a", roles=("platform_admin",))
    items = client.get("/api/v1/llm/providers", headers=pa).json()["data"]
    assert all(i["readonly"] is False for i in items)  # 平台管理员恒可编辑


# ---------- 跨租户隔离 ----------


def test_cross_tenant_write_404(client):
    ta, tb = _hdr("u-ta", tenant_id="t-a"), _hdr("u-tb", tenant_id="t-b")
    sid = _create(client, tb, name="b-private")["id"]

    r = client.put(f"/api/v1/llm/providers/{sid}", json={"name": "stolen"}, headers=ta)
    assert r.status_code == 404
    assert client.delete(f"/api/v1/llm/providers/{sid}", headers=ta).status_code == 404
    assert (
        client.post(
            "/api/v1/llm/providers/active", json={"supplier_id": sid}, headers=ta
        ).status_code
        == 404
    )
    names = {i["name"] for i in client.get("/api/v1/llm/providers", headers=ta).json()["data"]}
    assert "b-private" not in names


# ---------- schema 防伪造 ----------


def test_create_update_payload_rejects_tenant_id_field(client):
    hdr = _hdr("u-ta", tenant_id="t-a")
    r = client.post(
        "/api/v1/llm/providers",
        json={
            "name": "forged",
            "provider_type": "deepseek",
            "base_url": "https://api.deepseek.com",
            "model_id": "deepseek-chat",
            "api_key": TEST_KEY,
            "tenant_id": "t-victim",  # 伪造归属：extra=forbid 直接拒绝
        },
        headers=hdr,
    )
    # 平台全局校验处理器把 pydantic 校验错误统一映射为 400 INVALID_REQUEST
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_REQUEST"
    assert "tenant_id" in str(r.json())

    sid = _create(client, hdr)["id"]
    r = client.put(f"/api/v1/llm/providers/{sid}", json={"tenant_id": "t-victim"}, headers=hdr)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_REQUEST"


# ---------- 角色闸与平台回归 ----------


def test_non_admin_roles_still_forbidden(client):
    r = client.get("/api/v1/llm/providers", headers=_hdr("u-m", tenant_id="t-a", roles=("member",)))
    assert r.status_code == 403
    assert client.get("/api/v1/llm/providers").status_code == 401


def test_platform_admin_can_still_manage_global(client, db_session, global_sid):
    pa = _hdr("u-pa", roles=("platform_admin",))
    r = client.put(
        f"/api/v1/llm/providers/{global_sid}", json={"name": "global-renamed"}, headers=pa
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["name"] == "global-renamed"
    assert client.delete(f"/api/v1/llm/providers/{global_sid}", headers=pa).status_code == 200
