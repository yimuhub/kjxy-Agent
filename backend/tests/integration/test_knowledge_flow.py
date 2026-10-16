"""知识库集成测试：上传 txt → 同步解析 → 检索命中；解析失败分支；跨租户隔离；HTTP 路由冒烟。"""

import io

import app.modules.knowledge.models  # noqa: F401  (注册 ORM 表，供 create_all 建表)
import pytest
from app.core.config import settings
from app.core.deps import TenantContext, get_db_session, get_tenant_context
from app.core.errors import (
    AppError,
    app_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.core.middleware import RequestIDMiddleware
from app.modules.knowledge import service
from app.modules.knowledge.router import router as knowledge_router
from app.modules.knowledge.worker_tasks import parse_document_sync
from fastapi import FastAPI, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"

AGRI_TEXT = (
    "水稻分蘖期是决定有效穗数的关键时期。\n\n"
    "分蘖期田间管理要点：浅水勤灌，促进低位分蘖发生；"
    "当群体茎蘖数达到预期穗数的 80% 时及时晒田，控制无效分蘖。\n\n"
    "肥水管理：分蘖肥宜早施，一般在栽后 7 天左右施用；"
    "注意氮磷钾配合，避免偏施氮肥导致贪青晚熟。\n\n"
    "病虫害防治：重点防治二化螟、稻飞虱，坚持达标用药，"
    "优先选用高效低毒农药，并注意农药安全间隔期。\n\n"
    "晒田复水后保持干干湿湿，以湿为主，促进根系下扎，培育壮秆。\n\n"
)


def _ctx(tenant_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        user_id=f"user-{tenant_id}",
        roles=["tenant_admin"],
        request_id="req-test",
    )


def _upload_file(filename: str, data: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=filename)


@pytest.fixture()
def storage_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "local_storage_path", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def celery_memory_transport(monkeypatch):
    """测试内用 memory broker 投递解析任务，避免无 Redis 时的连接重试风暴。"""
    from app.workers.celery_app import celery_app

    monkeypatch.setattr(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setattr(celery_app.conf, "result_backend", "cache+memory://")


def _upload_and_parse(db_session, storage_tmp, tenant_id: str = TENANT_A):
    ctx = _ctx(tenant_id)
    doc = service.upload_document(
        db_session,
        ctx,
        _upload_file("shuidao.txt", AGRI_TEXT.encode("utf-8")),
        title="水稻分蘖期管理资料",
        request_id="req-1",
    )
    assert doc.status == "queued"
    parse_document_sync(db_session, doc.id)
    return service.get_document(db_session, ctx, doc.id)


def test_upload_parse_search_flow(db_session, storage_tmp):
    doc = _upload_and_parse(db_session, storage_tmp)
    assert doc.status == "ready"

    # Mock 向量是确定性的：用已入库 chunk 的原文精确查询，该 chunk 必为 top1
    from app.modules.knowledge.models import KnowledgeChunk

    chunks = (
        db_session.query(KnowledgeChunk)
        .filter(KnowledgeChunk.document_id == doc.id)
        .order_by(KnowledgeChunk.chunk_index)
        .all()
    )
    assert len(chunks) > 0
    target = chunks[0]

    hits = service.search(db_session, _ctx(TENANT_A), target.content, top_k=5)
    assert len(hits) > 0
    hit = hits[0]
    assert hit.chunk_id == target.id
    assert hit.score == pytest.approx(1.0)
    assert hit.document_id == doc.id
    assert hit.title == "水稻分蘖期管理资料"
    assert hit.source == "upload"
    assert hit.trust_level == "tenant_material"
    assert len(hit.quote) <= 200
    assert hit.quote

    # 普通关键词查询同样返回结果（不保证语义相关，Mock 向量无语义）
    hits2 = service.search(db_session, _ctx(TENANT_A), "水稻分蘖期田间管理", top_k=5)
    assert len(hits2) > 0


def test_parse_failure_branch(db_session, storage_tmp, monkeypatch):
    """损坏的 PDF → failed + 用户可读 error_message（不含堆栈）。"""
    ctx = _ctx(TENANT_A)
    doc = service.upload_document(
        db_session,
        ctx,
        _upload_file("broken.pdf", b"this is not a pdf file at all"),
        title="损坏的PDF",
        request_id="req-2",
    )
    parse_document_sync(db_session, doc.id)
    failed = service.get_document(db_session, ctx, doc.id)
    assert failed.status == "failed"
    assert failed.error_message
    assert "Traceback" not in failed.error_message
    assert "解析" in failed.error_message


def test_unsupported_file_type(db_session, storage_tmp):
    ctx = _ctx(TENANT_A)
    with pytest.raises(AppError) as exc_info:
        service.upload_document(
            db_session, ctx, _upload_file("evil.exe", b"MZ..."), title="x", request_id="req-3"
        )
    assert exc_info.value.code == "UNSUPPORTED_FILE_TYPE"
    assert exc_info.value.http_status == 415


def test_file_too_large(db_session, storage_tmp, monkeypatch):
    monkeypatch.setattr(settings, "upload_max_mb", 0)
    ctx = _ctx(TENANT_A)
    with pytest.raises(AppError) as exc_info:
        service.upload_document(
            db_session, ctx, _upload_file("a.txt", b"hello"), title="x", request_id="req-4"
        )
    assert exc_info.value.code == "FILE_TOO_LARGE"
    assert exc_info.value.http_status == 413


def test_cross_tenant_search_returns_empty(db_session, storage_tmp):
    _upload_and_parse(db_session, storage_tmp, tenant_id=TENANT_A)
    hits = service.search(db_session, _ctx(TENANT_B), "水稻分蘖期", top_k=5)
    assert hits == []


def _build_test_app(db_session, ctx: TenantContext) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
    app.include_router(knowledge_router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = lambda: db_session
    app.dependency_overrides[get_tenant_context] = lambda: ctx
    return app


def test_http_search_and_documents(db_session, storage_tmp):
    doc = _upload_and_parse(db_session, storage_tmp)
    app = _build_test_app(db_session, _ctx(TENANT_A))
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post("/api/v1/knowledge/search", json={"query": "水稻分蘖期管理", "top_k": 5})
        assert r.status_code == 200, r.text
        hits = r.json()["data"]["hits"]
        assert len(hits) > 0
        assert hits[0]["document_id"] == doc.id
        assert "request_id" in r.json()

        r = client.get("/api/v1/knowledge/documents")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["meta"]["total"] == 1
        assert body["data"][0]["status"] == "ready"

        r = client.get(f"/api/v1/knowledge/documents/{doc.id}")
        assert r.status_code == 200

        r = client.delete(f"/api/v1/knowledge/documents/{doc.id}")
        assert r.status_code == 200
        assert r.json()["data"]["deleted"] is True

        r = client.get(f"/api/v1/knowledge/documents/{doc.id}")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"


def test_http_upload_multipart(db_session, storage_tmp):
    app = _build_test_app(db_session, _ctx(TENANT_A))
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("note.txt", AGRI_TEXT.encode("utf-8"), "text/plain")},
            data={"title": "接口上传"},
        )
        assert r.status_code == 201, r.text
        assert r.json()["data"]["status"] == "queued"


def test_tool_registration_and_func(db_session, storage_tmp, monkeypatch):
    from app.modules.knowledge.service import register_knowledge_search_tool
    from app.modules.tools.registry import tool_registry

    tool_def = register_knowledge_search_tool()
    assert tool_def is not None
    assert tool_def.code == "knowledge.search"
    assert tool_def.risk_level == "read_only"
    assert tool_def.required_permissions == ["knowledge:read"]
    assert tool_registry.get("knowledge.search").code == "knowledge.search"

    doc = _upload_and_parse(db_session, storage_tmp)
    ctx = _ctx(TENANT_A)

    # 让工具函数使用测试会话而非默认 SessionLocal
    import app.db.session as session_module

    monkeypatch.setattr(session_module, "SessionLocal", lambda: db_session)
    result = tool_def.func({"query": "水稻分蘖期", "top_k": 3}, ctx)
    assert "hits" in result and len(result["hits"]) > 0
    assert result["hits"][0]["document_id"] == doc.id

    with pytest.raises(AppError) as exc_info:
        tool_def.func({"query": "", "top_k": 3}, ctx)
    assert exc_info.value.code == "TOOL_VALIDATION_FAILED"
