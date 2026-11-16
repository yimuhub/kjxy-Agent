"""成员读 /llm/providers 只拿选模型所需字段。"""

from types import SimpleNamespace

from app.modules.llm import supplier_router


def _supplier():
    return SimpleNamespace(
        id="sup-1",
        name="Agnes",
        provider_type="openai_compatible",
        model_id="agnes-3.0-flash",
        default_model_id="agnes-3.0-flash",
        models="agnes-3.0-flash,agnes-image-2.5-flash",
        channel_kind="user",
        status=1,
        is_active=True,
        base_url="https://internal.example.com/v1",
        api_key_masked="sk-55kN****STs",
        last_error="401 invalid key",
        price_in_per_1k=0.002,
    )


def test_member_provider_list_hides_internal_fields(monkeypatch):
    monkeypatch.setattr(
        supplier_router.supplier_service,
        "list_suppliers",
        lambda db, yard_id, is_platform_admin=False: [_supplier()],
    )
    user = SimpleNamespace(yard_id="yard-1", roles=["operator"], request_id="r-1")
    body = supplier_router.list_providers(ctx=(user, False), db=None, _key=None)
    row = body["data"][0]
    assert row["id"] == "sup-1"
    assert row["models"]
    assert row["base_url"] == ""
    assert row["api_key_masked"] == ""
    assert "last_error" not in row
    assert "price_in_per_1k" not in row


def test_admin_provider_list_keeps_management_fields(monkeypatch):
    from app.modules.llm.supplier_schemas import SupplierOut

    full = SupplierOut(
        id="sup-1",
        yard_id="yard-1",
        name="Agnes",
        provider_type="openai_compatible",
        base_url="https://internal.example.com/v1",
        model_id="agnes-3.0-flash",
        api_key_masked="sk-55kN****STs",
        models="agnes-3.0-flash",
        priority=10,
        status=1,
        is_active=True,
        created_at="2026-11-16T00:00:00+00:00",
        updated_at="2026-11-16T00:00:00+00:00",
    )
    monkeypatch.setattr(
        supplier_router.supplier_service,
        "list_suppliers",
        lambda db, yard_id, is_platform_admin=False: [full],
    )
    user = SimpleNamespace(yard_id="yard-1", roles=["yard_admin"], request_id="r-1")
    body = supplier_router.list_providers(ctx=(user, False), db=None, _key=None)
    assert body["data"][0]["base_url"] == "https://internal.example.com/v1"
