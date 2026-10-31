"""中转站目录同步（v3.33）：注入 fetcher、增删模型、停用被引用模型、缺密钥报错。"""

import httpx
import pytest
from app.core.errors import AppError
from app.modules.llm import relay_service
from app.modules.llm.binding_models import LLMBinding
from app.modules.llm.supplier_models import RelayChannel, Supplier, SupplierModel
from app.modules.llm.supplier_schemas import RelayChannelCreate

YARD = "yard-relay"


def _fetcher(catalog: dict, *, expected_key: str = "relay-key"):
    async def fetch(url: str, headers: dict[str, str]) -> httpx.Response:
        assert headers["Authorization"] == f"Bearer {expected_key}"
        assert url.endswith("/models")
        return httpx.Response(200, json=catalog)

    return fetch


def _channel(db, *, api_key: str | None = "relay-key") -> RelayChannel:
    out = relay_service.create_channel(
        db,
        YARD,
        RelayChannelCreate(
            name="私有中转",
            kind="private",
            base_url="https://relay.example.com",
            api_key=api_key,
            scope="yard",
        ),
        is_platform_admin=False,
    )
    return db.get(RelayChannel, out.id)


def _supplier_of(db, channel: RelayChannel) -> Supplier:
    row = db.query(Supplier).filter(Supplier.relay_channel_id == channel.id).first()
    assert row is not None
    return row


async def test_sync_creates_supplier_and_models(db_session):
    channel = _channel(db_session)
    relay_service.set_test_fetcher(
        _fetcher(
            {
                "data": [
                    {"id": "gpt-4o"},
                    {"id": "bge-m3"},
                    {
                        "id": "dall-e-3",
                        "modality": "image",
                        "pricing": {"input_per_1k": 0.02},
                    },
                ]
            }
        )
    )
    try:
        result = await relay_service.sync_relay_catalog(db_session, YARD, channel.id)
    finally:
        relay_service.set_test_fetcher(None)

    assert result.created == 3
    assert result.removed == 0
    supplier = _supplier_of(db_session, channel)
    assert supplier.provider_type == "official_relay"
    assert supplier.channel_kind == "relay"
    assert supplier.model_id == "gpt-4o"  # 第一个文本模型作为旧字段默认值
    rows = {
        row.model_key: row
        for row in db_session.query(SupplierModel)
        .filter(SupplierModel.supplier_id == supplier.id)
        .all()
    }
    assert set(rows) == {"gpt-4o", "bge-m3", "dall-e-3"}
    assert rows["bge-m3"].modality == "embedding"
    assert rows["dall-e-3"].protocol == "openai_images"
    assert all(row.source == "relay" for row in rows.values())
    channel = db_session.get(RelayChannel, channel.id)
    assert channel.status == "ok"
    assert channel.last_sync_at is not None


async def test_sync_updates_and_removes_models(db_session):
    channel = _channel(db_session)
    relay_service.set_test_fetcher(_fetcher({"data": [{"id": "gpt-4o"}, {"id": "bge-m3"}]}))
    try:
        await relay_service.sync_relay_catalog(db_session, YARD, channel.id)
    finally:
        relay_service.set_test_fetcher(None)
    supplier = _supplier_of(db_session, channel)

    # 目录变更：bge-m3 消失、gpt-4o 改名展示、新增 gpt-4o-mini
    relay_service.set_test_fetcher(
        _fetcher(
            {
                "data": [
                    {"id": "gpt-4o", "display_name": "GPT-4o 新名称"},
                    {"id": "gpt-4o-mini"},
                ]
            }
        )
    )
    try:
        result = await relay_service.sync_relay_catalog(db_session, YARD, channel.id)
    finally:
        relay_service.set_test_fetcher(None)

    assert result.created == 1
    assert result.updated == 1
    assert result.removed == 1
    removed = [item for item in result.items if item["action"] == "removed"]
    assert removed == [{"action": "removed", "model_key": "bge-m3", "mode": "deleted"}]
    rows = {
        row.model_key: row
        for row in db_session.query(SupplierModel)
        .filter(SupplierModel.supplier_id == supplier.id)
        .all()
    }
    assert set(rows) == {"gpt-4o", "gpt-4o-mini"}
    assert rows["gpt-4o"].display_name == "GPT-4o 新名称"


async def test_referenced_model_is_disabled_not_deleted(db_session):
    channel = _channel(db_session)
    relay_service.set_test_fetcher(
        _fetcher({"data": [{"id": "gpt-4o"}, {"id": "dall-e-3"}]})
    )
    try:
        await relay_service.sync_relay_catalog(db_session, YARD, channel.id)
    finally:
        relay_service.set_test_fetcher(None)
    supplier = _supplier_of(db_session, channel)
    image_row = (
        db_session.query(SupplierModel)
        .filter(
            SupplierModel.supplier_id == supplier.id,
            SupplierModel.model_key == "dall-e-3",
        )
        .one()
    )
    db_session.add(
        LLMBinding(
            yard_id=YARD,
            scope_type="global",
            scope_key="",
            supplier_id=supplier.id,
            supplier_model_id=image_row.id,
            priority=10,
            enabled=True,
        )
    )
    db_session.flush()
    image_id = image_row.id

    relay_service.set_test_fetcher(_fetcher({"data": [{"id": "gpt-4o"}]}))
    try:
        result = await relay_service.sync_relay_catalog(db_session, YARD, channel.id)
    finally:
        relay_service.set_test_fetcher(None)

    assert result.removed == 1
    assert result.items[-1] == {
        "action": "removed",
        "model_key": "dall-e-3",
        "mode": "disabled",
    }
    db_session.expire_all()
    row = db_session.get(SupplierModel, image_id)
    assert row is not None
    assert row.status == 2


async def test_sync_requires_api_key(db_session):
    channel = _channel(db_session, api_key=None)
    with pytest.raises(AppError) as exc:
        await relay_service.sync_relay_catalog(db_session, YARD, channel.id)
    assert exc.value.code == "RELAY_KEY_MISSING"
    assert "API Key" in exc.value.message


async def test_channel_key_never_returned(db_session):
    channel = _channel(db_session)
    out = relay_service.channel_to_out(channel)
    dumped = out.model_dump()
    assert "relay-key" not in str(dumped)
    assert dumped["api_key_masked"]
