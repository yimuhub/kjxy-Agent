"""模型供应商配置体系测试（A 组）。

- Fernet 加解密 / 打码 / 密钥缺失报错；
- suppliers CRUD（加密落库、打码返回、小院可见性）；
- 连通性探测（/models 优先、chat 降级、连续失败 3 次自动禁用）；
- get_provider() 热更新（小院 active → 全局 active → settings 回退）与缓存失效；
- 路由层：鉴权、Key 永不返回明文、模板接口。
"""

import os

import pytest

os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-min-32-chars-for-unit-tests")
os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("EMBEDDING_PROVIDER", "mock")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("SUPPLIER_KEY_SECRET", Fernet.generate_key().decode())

import httpx  # noqa: E402
from app.core.errors import AppError  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.modules.llm import provider as provider_module  # noqa: E402
from app.modules.llm import supplier_service  # noqa: E402
from app.modules.llm.supplier_crypto import (  # noqa: E402
    decrypt_api_key,
    encrypt_api_key,
    get_fernet,
    mask_api_key,
    reset_fernet_cache,
)
from app.modules.llm.supplier_models import Supplier, SupplierModel  # noqa: E402,F401 触发建表
from app.modules.llm.supplier_schemas import (  # noqa: E402
    SupplierCreate,
    SupplierModelCreate,
    SupplierUpdate,
)

YARD = "t-001"
OTHER_YARD = "t-002"
TEST_KEY = "sk-test-secret-key-12345"


def _payload(**kw):
    base = dict(
        name="deepseek-main",
        provider_type="deepseek",
        base_url="https://api.deepseek.com",
        model_id="deepseek-chat",
        api_key=TEST_KEY,
        priority=10,
    )
    base.update(kw)
    return SupplierCreate(**base)


@pytest.fixture()
def admin_headers():
    token = create_access_token(user_id="u-admin", yard_id=YARD, roles=["admin"])
    return {"Authorization": f"Bearer {token}"}


# ---------- 加密 ----------


def test_encrypt_decrypt_roundtrip():
    assert decrypt_api_key(encrypt_api_key(TEST_KEY)) == TEST_KEY


def test_mask_only_shows_head_tail():
    assert mask_api_key(TEST_KEY) == "sk-t****2345"
    assert mask_api_key("short") == "****"
    assert mask_api_key("") == "****"


def test_missing_secret_raises_with_hint(monkeypatch):
    monkeypatch.delenv("SUPPLIER_KEY_SECRET", raising=False)
    # 新读取顺序：settings（含 .env）优先、os.environ 兜底——两个来源都要清空
    from app.core.config import settings

    monkeypatch.setattr(settings, "supplier_key_secret", "", raising=False)
    reset_fernet_cache()
    try:
        with pytest.raises(AppError) as e:
            encrypt_api_key("x")
        assert e.value.code == "SUPPLIER_KEY_NOT_CONFIGURED"
        assert "SUPPLIER_KEY_SECRET" in e.value.message
    finally:
        monkeypatch.undo()  # 先恢复环境变量，再重建缓存
        reset_fernet_cache()
        get_fernet()


# ---------- CRUD ----------


def test_create_encrypts_and_masks(db_session):
    out = supplier_service.create_supplier(db_session, YARD, _payload(), is_platform_admin=False)
    db_session.commit()
    assert out.api_key_masked == "sk-t****2345"
    assert out.yard_id == YARD
    assert out.status == 1

    row = db_session.query(Supplier).one()
    assert row.api_key_enc != TEST_KEY  # 落库为密文
    assert decrypt_api_key(row.api_key_enc) == TEST_KEY


def test_list_visibility_global_plus_own(db_session):
    supplier_service.create_supplier(
        db_session, YARD, _payload(name="own"), is_platform_admin=False
    )
    supplier_service.create_supplier(
        db_session, OTHER_YARD, _payload(name="other"), is_platform_admin=False
    )
    supplier_service.create_supplier(
        db_session, None, _payload(name="shared", scope="global"), is_platform_admin=True
    )
    db_session.commit()

    names = {s.name for s in supplier_service.list_suppliers(db_session, YARD)}
    assert names == {"own", "shared"}  # 看不到 other 小院的


def test_create_global_requires_platform_admin(db_session):
    with pytest.raises(AppError) as e:
        supplier_service.create_supplier(
            db_session, YARD, _payload(scope="global"), is_platform_admin=False
        )
    assert e.value.code == "FORBIDDEN"


def test_update_rotates_key_and_resets_failures(db_session):
    out = supplier_service.create_supplier(db_session, YARD, _payload(), is_platform_admin=False)
    row = db_session.query(Supplier).one()
    row.consecutive_failures = 2
    db_session.flush()

    updated = supplier_service.update_supplier(
        db_session, YARD, out.id, SupplierUpdate(api_key="sk-new-key-99999", priority=5)
    )
    db_session.commit()
    assert updated.api_key_masked == "sk-n****9999"
    assert updated.priority == 5
    row = db_session.query(Supplier).one()
    assert decrypt_api_key(row.api_key_enc) == "sk-new-key-99999"
    assert row.consecutive_failures == 0  # 轮换后清零


def test_update_without_key_keeps_old(db_session):
    out = supplier_service.create_supplier(db_session, YARD, _payload(), is_platform_admin=False)
    updated = supplier_service.update_supplier(
        db_session, YARD, out.id, SupplierUpdate(name="renamed")
    )
    assert updated.name == "renamed"
    assert updated.api_key_masked == "sk-t****2345"
    row = db_session.query(Supplier).one()
    assert decrypt_api_key(row.api_key_enc) == TEST_KEY


def test_create_with_available_models_imports_rows(db_session):
    """创建供应商时勾选多个模型：一次性落 supplier_models，默认模型去重置首。"""
    from app.modules.llm.supplier_schemas import STATUS_ENABLED

    out = supplier_service.create_supplier(
        db_session,
        YARD,
        _payload(
            model_id="deepseek-chat",
            available_models=["deepseek-chat", "deepseek-reasoner", "deepseek-chat"],
        ),
        is_platform_admin=False,
    )
    db_session.commit()

    rows = (
        db_session.query(SupplierModel)
        .filter(SupplierModel.supplier_id == out.id)
        .order_by(SupplierModel.created_at.asc())
        .all()
    )
    assert {r.upstream_model_id for r in rows} == {"deepseek-chat", "deepseek-reasoner"}
    assert all(r.status == STATUS_ENABLED for r in rows)
    assert out.model_id == "deepseek-chat"
    default_row = next(r for r in rows if r.upstream_model_id == "deepseek-chat")
    assert out.default_model_id == default_row.id
    assert set((out.models or "").split(",")) == {"deepseek-chat", "deepseek-reasoner"}


def test_default_model_always_added_to_available_models(db_session):
    """默认模型没勾选也要自动补进可用列表，避免默认模型无对应行。"""
    out = supplier_service.create_supplier(
        db_session,
        YARD,
        _payload(model_id="deepseek-chat", available_models=["deepseek-reasoner"]),
        is_platform_admin=False,
    )
    db_session.commit()
    names = {
        r.upstream_model_id
        for r in db_session.query(SupplierModel).filter(SupplierModel.supplier_id == out.id)
    }
    assert names == {"deepseek-chat", "deepseek-reasoner"}
    assert out.default_model_id is not None


def test_update_available_models_adds_and_disables(db_session):
    """编辑时重新勾选：新增模型落行，取消勾选的旧模型置停用而不是删除。"""
    from app.modules.llm.supplier_schemas import STATUS_DISABLED, STATUS_ENABLED

    out = supplier_service.create_supplier(
        db_session,
        YARD,
        _payload(model_id="m1", available_models=["m1", "m2"]),
        is_platform_admin=False,
    )
    db_session.commit()

    supplier_service.update_supplier(
        db_session,
        YARD,
        out.id,
        SupplierUpdate(model_id="m3", available_models=["m1", "m3"]),
    )
    db_session.commit()

    rows = {
        r.upstream_model_id: r
        for r in db_session.query(SupplierModel).filter(SupplierModel.supplier_id == out.id)
    }
    assert set(rows) == {"m1", "m2", "m3"}  # m2 未删除
    assert rows["m1"].status == STATUS_ENABLED
    assert rows["m3"].status == STATUS_ENABLED
    assert rows["m2"].status == STATUS_DISABLED

    refreshed = db_session.query(Supplier).filter(Supplier.id == out.id).one()
    assert refreshed.model_id == "m3"
    assert refreshed.default_model_id == rows["m3"].id


def test_update_omitting_available_models_keeps_selection(db_session):
    """不传 available_models 表示不改动已有勾选（兼容旧客户端）。"""
    from app.modules.llm.supplier_schemas import STATUS_ENABLED

    out = supplier_service.create_supplier(
        db_session,
        YARD,
        _payload(model_id="m1", available_models=["m1", "m2"]),
        is_platform_admin=False,
    )
    db_session.commit()

    supplier_service.update_supplier(db_session, YARD, out.id, SupplierUpdate(priority=3))
    db_session.commit()

    enabled = {
        r.upstream_model_id
        for r in db_session.query(SupplierModel).filter(
            SupplierModel.supplier_id == out.id,
            SupplierModel.status == STATUS_ENABLED,
        )
    }
    assert enabled == {"m1", "m2"}


def test_delete_removes_row(db_session):
    out = supplier_service.create_supplier(db_session, YARD, _payload(), is_platform_admin=False)
    supplier_service.delete_supplier(db_session, YARD, out.id)
    db_session.commit()
    assert db_session.query(Supplier).count() == 0


def test_enable_disable(db_session):
    from app.modules.llm.supplier_schemas import STATUS_DISABLED, STATUS_ENABLED

    out = supplier_service.create_supplier(db_session, YARD, _payload(), is_platform_admin=False)
    assert (
        supplier_service.set_supplier_status(db_session, YARD, out.id, STATUS_DISABLED).status
        == STATUS_DISABLED
    )
    assert (
        supplier_service.set_supplier_status(db_session, YARD, out.id, STATUS_ENABLED).status
        == STATUS_ENABLED
    )


# ---------- active 解析 ----------


def test_resolve_prefers_yard_active_then_global(db_session):
    yard_row = supplier_service.create_supplier(
        db_session, YARD, _payload(name="yard-one", priority=1), is_platform_admin=False
    )
    supplier_service.create_supplier(
        db_session,
        None,
        _payload(name="global-high", priority=100, scope="global"),
        is_platform_admin=True,
    )
    db_session.commit()

    # 未设置 active：走全局最高优先级
    assert supplier_service.resolve_active_supplier(db_session, YARD).name == "global-high"
    # 设置小院 active 后优先
    supplier_service.set_active_supplier(db_session, YARD, yard_row.id)
    db_session.commit()
    assert supplier_service.resolve_active_supplier(db_session, YARD).name == "yard-one"


def test_resolve_disabled_active_falls_back_to_global(db_session):
    from app.modules.llm.supplier_schemas import STATUS_DISABLED

    yard_row = supplier_service.create_supplier(
        db_session, YARD, _payload(name="yard-one"), is_platform_admin=False
    )
    supplier_service.create_supplier(
        db_session, None, _payload(name="global-one", scope="global"), is_platform_admin=True
    )
    supplier_service.set_active_supplier(db_session, YARD, yard_row.id)
    supplier_service.set_supplier_status(db_session, YARD, yard_row.id, STATUS_DISABLED)
    db_session.commit()
    assert supplier_service.resolve_active_supplier(db_session, YARD).name == "global-one"


def test_resolve_none_when_no_supplier(db_session):
    assert supplier_service.resolve_active_supplier(db_session, YARD) is None


def test_set_active_rejects_disabled(db_session):
    from app.modules.llm.supplier_schemas import STATUS_DISABLED

    out = supplier_service.create_supplier(db_session, YARD, _payload(), is_platform_admin=False)
    supplier_service.set_supplier_status(db_session, YARD, out.id, STATUS_DISABLED)
    with pytest.raises(AppError) as e:
        supplier_service.set_active_supplier(db_session, YARD, out.id)
    assert e.value.code == "SUPPLIER_DISABLED"


# ---------- 探测 ----------


class _FakeResp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeClient:
    mode = "models_ok"

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        if _FakeClient.mode == "models_ok":
            return _FakeResp(200, {"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]})
        if _FakeClient.mode in ("models_401", "chat_ok"):
            return _FakeResp(401)
        raise httpx.ConnectError("unreachable")

    async def post(self, url, json=None, headers=None):
        if _FakeClient.mode in ("models_401", "chat_ok"):
            return _FakeResp(200, {"choices": [{"message": {"content": "hi"}}]})
        if _FakeClient.mode == "chat_401":
            return _FakeResp(401)
        raise httpx.ConnectError("unreachable")


@pytest.fixture()
def fake_httpx(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    _FakeClient.mode = "models_ok"
    yield _FakeClient


@pytest.mark.asyncio
async def test_probe_models_ok(fake_httpx):
    result = await supplier_service.probe_upstream("https://x", "k", "m")
    assert result.ok is True
    assert result.models == ["deepseek-chat", "deepseek-reasoner"]
    assert result.latency_ms >= 0


@pytest.mark.asyncio
async def test_probe_falls_back_to_chat(fake_httpx):
    fake_httpx.mode = "models_401"
    result = await supplier_service.probe_upstream("https://x", "k", "m")
    assert result.ok is True
    assert result.models == []
    assert "chat" in result.note


@pytest.mark.asyncio
async def test_probe_auth_failure(fake_httpx):
    fake_httpx.mode = "chat_401"
    result = await supplier_service.probe_upstream("https://x", "bad-key", "m")
    assert result.ok is False
    assert "鉴权" in result.note


@pytest.mark.asyncio
async def test_probe_connect_error(fake_httpx):
    fake_httpx.mode = "unreachable"
    result = await supplier_service.probe_upstream("https://x", "k", "m")
    assert result.ok is False


@pytest.mark.asyncio
async def test_saved_test_writes_back_and_auto_disables_after_3(db_session, fake_httpx):
    from app.modules.llm.supplier_schemas import STATUS_DISABLED

    out = supplier_service.create_supplier(db_session, YARD, _payload(), is_platform_admin=False)
    fake_httpx.mode = "unreachable"
    for _ in range(3):
        _out, result = await supplier_service.test_saved_supplier(db_session, YARD, out.id)
        assert result.ok is False
    db_session.commit()
    row = db_session.query(Supplier).one()
    assert row.status == STATUS_DISABLED  # 连续 3 次失败自动禁用
    assert row.test_time is not None
    assert row.response_time_ms is not None
    assert row.last_error


@pytest.mark.asyncio
async def test_saved_test_success_resets_failures(db_session, fake_httpx):
    out = supplier_service.create_supplier(db_session, YARD, _payload(), is_platform_admin=False)
    row = db_session.query(Supplier).one()
    row.consecutive_failures = 2
    db_session.flush()
    _out, result = await supplier_service.test_saved_supplier(db_session, YARD, out.id)
    assert result.ok is True
    row = db_session.query(Supplier).one()
    assert row.consecutive_failures == 0
    assert row.last_error is None


# ---------- 热更新 ----------


def test_get_provider_uses_active_supplier(db_session):
    out = supplier_service.create_supplier(db_session, YARD, _payload(), is_platform_admin=False)
    supplier_service.set_active_supplier(db_session, YARD, out.id)
    db_session.commit()

    with provider_module.provider_resolution(YARD, db_session):
        provider = provider_module.get_provider()
    # 解密后的 key 注入到 Provider，且调用记录标签带供应商名
    assert provider.api_key == TEST_KEY
    assert provider.model == "deepseek-chat"
    assert provider.base_url == "https://api.deepseek.com"
    assert provider.provider_name == "supplier:deepseek-main"


def test_get_provider_falls_back_to_settings_without_supplier(db_session):
    with provider_module.provider_resolution(YARD, db_session):
        provider = provider_module.get_provider()
    assert type(provider).__name__ == "FakeProvider"  # settings.llm_provider=fake


def test_cache_invalidated_on_update(db_session):
    out = supplier_service.create_supplier(db_session, YARD, _payload(), is_platform_admin=False)
    supplier_service.set_active_supplier(db_session, YARD, out.id)
    db_session.commit()

    with provider_module.provider_resolution(YARD, db_session):
        p1 = provider_module.get_provider()
        assert p1.api_key == TEST_KEY
        # 轮换密钥后缓存失效，新实例使用新 key
        supplier_service.update_supplier(
            db_session, YARD, out.id, SupplierUpdate(api_key="sk-rotated-00001")
        )
        db_session.commit()
        p2 = provider_module.get_provider()
    assert p2 is not p1
    assert p2.api_key == "sk-rotated-00001"


def test_cache_invalidated_on_delete(db_session):
    out = supplier_service.create_supplier(db_session, YARD, _payload(), is_platform_admin=False)
    supplier_service.set_active_supplier(db_session, YARD, out.id)
    db_session.commit()
    with provider_module.provider_resolution(YARD, db_session):
        assert provider_module.get_provider().api_key == TEST_KEY
    supplier_service.delete_supplier(db_session, YARD, out.id)
    db_session.commit()
    with provider_module.provider_resolution(YARD, db_session):
        assert type(provider_module.get_provider()).__name__ == "FakeProvider"


# ---------- 模板 ----------


def test_templates_have_no_keys():
    templates = supplier_service.get_provider_templates()
    assert {t.provider_type for t in templates} == {
        "deepseek",
        "qwen",
        "moonshot",
        "openai",
        "openai_compatible",
        # v3.26 视觉域：图像/视频供应商与对话供应商分开配置
        "openai_images",
        "dashscope_image",
        "openai_video",
    }
    for t in templates:
        assert "api_key" not in t.model_dump()


# ---------- 路由层 ----------


def test_router_create_and_list_masked(client, admin_headers):
    r = client.post(
        "/api/v1/llm/providers",
        json={
            "name": "deepseek-main",
            "provider_type": "deepseek",
            "base_url": "https://api.deepseek.com",
            "model_id": "deepseek-chat",
            "api_key": TEST_KEY,
        },
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["api_key_masked"] == "sk-t****2345"
    assert TEST_KEY not in r.text  # 明文 key 永不出现在响应中
    assert "api_key" not in body or body.get("api_key") is None

    r = client.get("/api/v1/llm/providers", headers=admin_headers)
    assert r.status_code == 200
    items = r.json()["data"]
    assert len(items) == 1
    assert items[0]["api_key_masked"] == "sk-t****2345"


def test_router_requires_admin(client):
    user_token = create_access_token(user_id="u1", yard_id=YARD, roles=["member"])
    r = client.get("/api/v1/llm/providers", headers={"Authorization": f"Bearer {user_token}"})
    assert r.status_code == 403
    r = client.get("/api/v1/llm/providers")
    assert r.status_code == 401


def test_router_test_candidate_does_not_persist(client, admin_headers, monkeypatch):
    async def fake_probe(base_url, api_key, model_id, timeout=10, *, provider_type=""):
        assert api_key == TEST_KEY  # key 透传到探测
        from app.modules.llm.supplier_schemas import SupplierTestResult

        return SupplierTestResult(ok=True, latency_ms=12, models=["m1"], note="ok")

    monkeypatch.setattr(supplier_service, "probe_upstream", fake_probe)
    r = client.post(
        "/api/v1/llm/providers/test",
        json={
            "provider_type": "deepseek",
            "base_url": "https://api.deepseek.com",
            "model_id": "deepseek-chat",
            "api_key": TEST_KEY,
        },
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["ok"] is True and data["models"] == ["m1"]
    assert TEST_KEY not in r.text


def test_router_templates(client, admin_headers):
    r = client.get("/api/v1/llm/provider-templates", headers=admin_headers)
    assert r.status_code == 200
    items = r.json()["data"]
    assert len(items) == 8
    deepseek = next(t for t in items if t["provider_type"] == "deepseek")
    assert deepseek["base_url"] == "https://api.deepseek.com"


def test_router_set_active_and_enable_disable(client, admin_headers):
    r = client.post(
        "/api/v1/llm/providers",
        json={
            "name": "qwen-1",
            "provider_type": "qwen",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model_id": "qwen-max",
            "api_key": "sk-qwen-key-abcd1234",
        },
        headers=admin_headers,
    )
    sid = r.json()["data"]["id"]

    r = client.post(
        "/api/v1/llm/providers/active", json={"supplier_id": sid}, headers=admin_headers
    )
    assert r.status_code == 200
    assert r.json()["data"]["is_active"] is True

    r = client.post(f"/api/v1/llm/providers/{sid}/disable", headers=admin_headers)
    assert r.json()["data"]["status"] == 2
    r = client.post(f"/api/v1/llm/providers/{sid}/enable", headers=admin_headers)
    assert r.json()["data"]["status"] == 1

    r = client.delete(f"/api/v1/llm/providers/{sid}", headers=admin_headers)
    assert r.status_code == 200
    r = client.get("/api/v1/llm/providers", headers=admin_headers)
    assert r.json()["data"] == []


@pytest.mark.asyncio
async def test_provider_honours_per_request_timeout(monkeypatch):
    """长产出（整份方案）按请求放宽超时；不带该字段时仍用供应商/全局默认值。"""
    from app.modules.llm.providers_openai import OpenAICompatibleProvider
    from app.modules.llm.schemas import ChatMessage as _Msg
    from app.modules.llm.schemas import LLMRequest as _Req

    seen: list[object] = []

    class _RecordingClient:
        def __init__(self, *a, **kw):
            seen.append(kw.get("timeout"))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            return _FakeResp(
                200,
                {
                    "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    "model": "m",
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", _RecordingClient)
    provider = OpenAICompatibleProvider(base_url="https://x", api_key="k", model="m")
    provider.timeout = 60

    await provider.complete(_Req(messages=[_Msg(role="user", content="写")]))
    await provider.complete(_Req(messages=[_Msg(role="user", content="写")], timeout_seconds=180))
    assert seen == [60, 180]


# ---------- 价格选填与模态识别（v3.36.2） ----------


def test_model_pricing_allows_unit_and_optional_numbers(db_session):
    """价格是选填：unit 是字符串，input/output 留空不应报「必须是数字」。"""
    supplier = supplier_service.create_supplier(
        db_session, YARD, _payload(), is_platform_admin=False
    )
    out = supplier_service.create_supplier_model(
        db_session,
        YARD,
        supplier.id,
        SupplierModelCreate(
            model_key="priced-model",
            upstream_model_id="priced-model",
            pricing={"unit": "per_1k_tokens", "input": None, "output": None, "currency": "CNY"},
        ),
    )
    assert out.pricing == {"unit": "per_1k_tokens", "currency": "CNY"}


@pytest.mark.asyncio
async def test_detect_modality_from_model_name(db_session):
    supplier = supplier_service.create_supplier(
        db_session,
        YARD,
        _payload(
            provider_type="openai_compatible",
            base_url="https://example.com/v1",
            model_id="gpt-image-1",
        ),
        is_platform_admin=False,
    )
    result = await supplier_service.detect_model_modality(
        db_session, YARD, supplier.id, upstream_model_id="gpt-image-1"
    )
    assert result.modality == "image"
    assert result.source == "local"


@pytest.mark.asyncio
async def test_detect_modality_from_upstream_catalog(db_session):
    """模型名识别不出时，用上游目录里的 architecture.output_modalities 兜底。"""
    supplier = supplier_service.create_supplier(
        db_session,
        YARD,
        _payload(
            provider_type="openai_compatible",
            base_url="https://example.com/v1",
            model_id="custom-x",
        ),
        is_platform_admin=False,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "custom-x",
                        "architecture": {
                            "input_modalities": ["text"],
                            "output_modalities": ["image"],
                        },
                    }
                ]
            },
        )

    supplier_service.set_test_transport(httpx.MockTransport(handler))
    try:
        result = await supplier_service.detect_model_modality(
            db_session, YARD, supplier.id, upstream_model_id="custom-x"
        )
    finally:
        supplier_service.set_test_transport(None)
    assert result.modality == "image"
    assert result.source == "catalog"
    assert result.matched_id == "custom-x"


@pytest.mark.asyncio
async def test_detect_modality_unknown_when_catalog_has_no_metadata(db_session):
    supplier = supplier_service.create_supplier(
        db_session,
        YARD,
        _payload(
            provider_type="openai_compatible",
            base_url="https://example.com/v1",
            model_id="mystery-x",
        ),
        is_platform_admin=False,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "mystery-x"}]})

    supplier_service.set_test_transport(httpx.MockTransport(handler))
    try:
        result = await supplier_service.detect_model_modality(
            db_session, YARD, supplier.id, upstream_model_id="mystery-x"
        )
    finally:
        supplier_service.set_test_transport(None)
    assert result.source == "unknown"
    assert "手动选择" in (result.message or "")
