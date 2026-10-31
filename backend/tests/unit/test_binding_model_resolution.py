"""绑定解析（v3.33）：模型行优先、旧 model_name 兼容、默认模型回落与 Provider 构建。"""

import pytest
from app.core.errors import AppError
from app.modules.llm import binding_service, images, supplier_service
from app.modules.llm.binding_schemas import BindingEntry, BindingsReplace
from app.modules.llm.supplier_crypto import encrypt_api_key
from app.modules.llm.supplier_models import Supplier, SupplierModel
from app.modules.llm.supplier_schemas import SupplierModelCreate

YARD = "yard-binding"


def _make_supplier(db) -> Supplier:
    row = Supplier(
        yard_id=YARD,
        name="multi-model",
        provider_type="openai_compatible",
        base_url="https://api.example.com/v1",
        model_id="legacy-model",
        api_key_enc=encrypt_api_key("sk-binding-secret"),
        status=1,
        channel_kind="user",
    )
    db.add(row)
    db.flush()
    return row


def _add_model(db, supplier: Supplier, **kwargs):
    payload = SupplierModelCreate(**kwargs)
    return supplier_service.create_supplier_model(
        db, YARD, supplier.id, payload, is_platform_admin=False
    )


def _bind(db, supplier: Supplier, entry: BindingEntry):
    return binding_service.replace_bindings(
        db,
        YARD,
        BindingsReplace(scope_type="global", entries=[entry]),
        is_platform_admin=False,
    )


def test_supplier_model_id_wins_over_model_name(db_session):
    supplier = _make_supplier(db_session)
    reasoner = _add_model(
        db_session,
        supplier,
        model_key="reasoner",
        upstream_model_id="reasoner-v1",
        pricing={"input_per_1k": 0.1, "output_per_1k": 0.2},
        capabilities={"tool_calling": True},
    )
    vision = _add_model(
        db_session,
        supplier,
        model_key="vision",
        upstream_model_id="vision-v1",
        modality="multimodal",
    )

    _bind(
        db_session,
        supplier,
        BindingEntry(
            supplier_id=supplier.id,
            supplier_model_id=vision.id,
            model_name="reasoner-v1",  # 显式模型行优先于旧字段
        ),
    )
    chain = binding_service.resolve_chain(db_session, YARD)
    assert len(chain) == 1
    candidate = chain[0]
    assert candidate.supplier_model is not None
    assert candidate.supplier_model.id == vision.id
    assert candidate.model_name == "vision-v1"

    provider = supplier_service.build_provider_from_supplier(
        supplier, model_row=db_session.get(SupplierModel, reasoner.id)
    )
    # 模型行单价与能力位覆盖供应商级配置
    assert provider.price_in_per_1k == 0.1
    assert provider.price_out_per_1k == 0.2
    assert provider.supports_tools is True
    assert provider.model == "reasoner-v1"
    assert provider.supplier_model_id == reasoner.id


def test_legacy_model_name_still_resolves_to_model_row(db_session):
    supplier = _make_supplier(db_session)
    _add_model(
        db_session,
        supplier,
        model_key="reasoner",
        upstream_model_id="reasoner-v1",
    )
    vision = _add_model(
        db_session,
        supplier,
        model_key="vision",
        upstream_model_id="vision-v1",
        modality="multimodal",
    )

    # 旧前端只写 model_name（这里给上游物理名）
    _bind(db_session, supplier, BindingEntry(supplier_id=supplier.id, model_name="vision-v1"))
    chain = binding_service.resolve_chain(db_session, YARD)
    assert chain[0].supplier_model.id == vision.id
    assert chain[0].model_name == "vision-v1"

    # 也接受逻辑名 model_key 的旧写法
    _bind(db_session, supplier, BindingEntry(supplier_id=supplier.id, model_name="reasoner"))
    chain = binding_service.resolve_chain(db_session, YARD)
    assert chain[0].supplier_model.model_key == "reasoner"


def test_default_model_id_fallback(db_session):
    supplier = _make_supplier(db_session)
    first = _add_model(
        db_session, supplier, model_key="first", upstream_model_id="first-v1"
    )
    _add_model(db_session, supplier, model_key="second", upstream_model_id="second-v1")

    # 不指定模型：回落到供应商默认模型（首条文本模型）
    _bind(db_session, supplier, BindingEntry(supplier_id=supplier.id))
    chain = binding_service.resolve_chain(db_session, YARD)
    assert chain[0].supplier_model.id == first.id
    assert chain[0].model_name == "first-v1"
    assert db_session.get(Supplier, supplier.id).default_model_id == first.id

    summary = binding_service.resolution_summary(db_session, YARD)
    assert summary["resolved_model"] == "first-v1"
    assert summary["resolved_model_key"] == "first"


def test_deleting_bound_model_returns_409(db_session):
    supplier = _make_supplier(db_session)
    row = _add_model(
        db_session, supplier, model_key="bound", upstream_model_id="bound-v1"
    )
    _bind(
        db_session,
        supplier,
        BindingEntry(supplier_id=supplier.id, supplier_model_id=row.id),
    )

    with pytest.raises(AppError) as exc:
        supplier_service.delete_supplier_model(db_session, YARD, supplier.id, row.id)
    assert exc.value.code == "MODEL_IN_USE"
    assert exc.value.http_status == 409
    assert "绑定" in exc.value.message


def test_image_provider_uses_model_row_protocol(db_session):
    supplier = _make_supplier(db_session)
    supplier.provider_type = "official_relay"
    image_row = _add_model(
        db_session,
        supplier,
        model_key="dall-e-3",
        upstream_model_id="dall-e-3-2024",
        modality="image",
        protocol="openai_images",
    )

    provider = images.build_image_provider(
        supplier, model_row=db_session.get(SupplierModel, image_row.id)
    )
    assert isinstance(provider, images.OpenAIImagesProvider)
    assert provider.model == "dall-e-3-2024"

    # 图像能力层绑定到该模型后，解析链应按模型行协议选中它
    binding_service.replace_bindings(
        db_session,
        YARD,
        BindingsReplace(
            scope_type="purpose",
            scope_key="image_generation",
            entries=[
                BindingEntry(supplier_id=supplier.id, supplier_model_id=image_row.id)
            ],
        ),
        is_platform_admin=False,
    )
    resolved = images.resolve_image_provider(db_session, YARD)
    assert resolved is not None
    resolved_provider, level = resolved
    assert isinstance(resolved_provider, images.OpenAIImagesProvider)
    assert level == binding_service.LEVEL_PURPOSE_BINDING
    assert resolved_provider.model == "dall-e-3-2024"
