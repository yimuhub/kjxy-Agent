"""供应商多模型导入（v3.33）：dry-run、幂等、模态推断、非法行与密钥不外泄。"""

import json

import httpx
import pytest
from app.core.errors import AppError
from app.modules.llm import supplier_service
from app.modules.llm.supplier_crypto import encrypt_api_key
from app.modules.llm.supplier_models import Supplier, SupplierModel
from app.modules.llm.supplier_schemas import (
    SupplierImportBundle,
    SupplierImportPreview,
    SupplierImportResult,
    SupplierModelCreate,
)

YARD = "yard-import"


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("bge-m3", "embedding"),
        ("bge-reranker-v2", "rerank"),
        ("whisper-1", "asr"),
        ("paraformer-v2", "asr"),
        ("dall-e-3", "image"),
        ("wanx2.1-t2i", "image"),
        ("veo-3", "video"),
        ("kling-v2", "video"),
        ("qwen-vl-max", "multimodal"),
        ("gpt-4o", "text"),
    ],
)
def test_guess_modality_by_keyword(model_id, expected):
    assert supplier_service.guess_modality(model_id) == expected


async def test_probe_models_reports_guessed_modality():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "bge-m3"}, {"id": "gpt-4o"}]})

    supplier_service.set_test_transport(httpx.MockTransport(handler))
    try:
        result = await supplier_service.probe_models(
            provider_type="openai_compatible", base_url="http://127.0.0.1:11434/v1"
        )
    finally:
        supplier_service.set_test_transport(None)

    assert result.source == "remote"
    assert [(m.id, m.guessed_modality) for m in result.models] == [
        ("bge-m3", "embedding"),
        ("gpt-4o", "text"),
    ]


def _make_supplier(db, name: str = "import-src") -> Supplier:
    row = Supplier(
        yard_id=YARD,
        name=name,
        provider_type="openai_compatible",
        base_url="https://api.example.com/v1",
        model_id="placeholder",
        api_key_enc=encrypt_api_key("sk-super-secret-1234"),
        status=1,
        channel_kind="user",
    )
    db.add(row)
    db.flush()
    return row


def test_dry_run_does_not_write(db_session):
    supplier = _make_supplier(db_session)
    bundle = SupplierImportBundle(
        models=[SupplierModelCreate(model_key="gpt-4o", upstream_model_id="gpt-4o-2024-08-06")]
    )

    preview = supplier_service.import_models(db_session, YARD, supplier.id, bundle, dry_run=True)

    assert isinstance(preview, SupplierImportPreview)
    assert preview.summary["create"] == 1
    assert preview.items[0].action == "create"
    assert db_session.query(SupplierModel).count() == 0


def test_commit_is_idempotent_and_guesses_modality(db_session):
    supplier = _make_supplier(db_session)
    content = json.dumps(
        [
            {"id": "bge-m3", "display_name": "BGE M3"},
            {"id": "gpt-4o-mini"},
            {"id": "dall-e-3"},
        ]
    )
    bundle = SupplierImportBundle(content=content)

    first = supplier_service.import_models(db_session, YARD, supplier.id, bundle, dry_run=False)
    assert isinstance(first, SupplierImportResult)
    assert first.created == 3
    assert first.errors == []

    rows = {
        row.model_key: row
        for row in db_session.query(SupplierModel)
        .filter(SupplierModel.supplier_id == supplier.id)
        .all()
    }
    assert rows["bge-m3"].modality == "embedding"
    assert rows["gpt-4o-mini"].modality == "text"
    assert rows["dall-e-3"].modality == "image"
    assert rows["dall-e-3"].protocol == "openai_images"
    assert all(row.source == "import" for row in rows.values())

    second = supplier_service.import_models(db_session, YARD, supplier.id, bundle, dry_run=False)
    assert second.created == 0
    assert second.updated == 0
    assert second.skipped == 3
    assert db_session.query(SupplierModel).count() == 3


def test_invalid_rows_never_persist(db_session):
    supplier = _make_supplier(db_session)
    content = json.dumps(
        [
            {
                "model_key": "bad-price",
                "upstream_model_id": "bad-price",
                "pricing": {"input_per_1k": -0.1},
            },
            {"model_key": "", "upstream_model_id": "missing-key"},
            {"model_key": "good-model", "upstream_model_id": "good-model"},
        ]
    )

    result = supplier_service.import_models(
        db_session, YARD, supplier.id, SupplierImportBundle(content=content)
    )

    assert isinstance(result, SupplierImportResult)
    assert result.created == 1
    assert len(result.errors) == 2
    keys = [row.model_key for row in db_session.query(SupplierModel).all()]
    assert keys == ["good-model"]


def test_duplicate_rows_are_conflict_not_overwrite(db_session):
    supplier = _make_supplier(db_session)
    content = json.dumps(
        [
            {"model_key": "dup", "upstream_model_id": "dup-1"},
            {"model_key": "dup", "upstream_model_id": "dup-2"},
        ]
    )

    preview = supplier_service.import_models(
        db_session, YARD, supplier.id, SupplierImportBundle(content=content), dry_run=True
    )

    assert isinstance(preview, SupplierImportPreview)
    assert preview.summary["conflict"] == 1
    assert preview.summary["create"] == 1


def test_api_key_never_leaks_in_preview_or_result(db_session):
    secret = "sk-must-not-appear-9f9f"
    bundle = SupplierImportBundle.model_validate(
        {
            "supplier": {
                "name": "new-supplier",
                "provider_type": "openai_compatible",
                "base_url": "https://api.example.com/v1",
                "api_key": secret,
            },
            "models": [
                {
                    "model_key": "m1",
                    "upstream_model_id": "m1-upstream",
                    "modality": "text",
                }
            ],
        }
    )

    preview = supplier_service.import_models(db_session, YARD, None, bundle, dry_run=True)
    assert isinstance(preview, SupplierImportPreview)
    assert secret not in json.dumps(preview.model_dump(), ensure_ascii=False)
    assert preview.supplier_action == "create"

    result = supplier_service.import_models(db_session, YARD, None, bundle)
    assert isinstance(result, SupplierImportResult)
    assert secret not in json.dumps(result.model_dump(), ensure_ascii=False)
    created = db_session.get(Supplier, result.supplier_id)
    assert created is not None
    assert created.name == "new-supplier"
    # 密钥只以密文落库
    assert created.api_key_enc != secret
    assert supplier_service.decrypt_api_key(created.api_key_enc) == secret


def test_create_supplier_requires_credentials(db_session):
    bundle = SupplierImportBundle(
        supplier={"name": "no-key", "provider_type": "openai_compatible"},
    )
    with pytest.raises(AppError) as exc:
        supplier_service.import_models(db_session, YARD, None, bundle, dry_run=True)
    assert exc.value.code == "INVALID_REQUEST"


async def test_import_url_rejects_non_http_scheme():
    with pytest.raises(AppError) as exc:
        await supplier_service.fetch_import_text("file:///etc/passwd")
    assert exc.value.code == "INVALID_REQUEST"


async def test_import_url_blocks_metadata_address():
    with pytest.raises(AppError) as exc:
        await supplier_service.fetch_import_text("http://169.254.169.254/latest/meta-data/")
    assert exc.value.code == "SSRF_BLOCKED"
