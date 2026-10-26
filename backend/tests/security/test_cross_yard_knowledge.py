"""跨小院安全测试（service 层，不走 HTTP，不依赖 auth 模块）。

用 core.security.create_access_token 伪造两个小院的身份令牌，
由令牌解出 YardContext，再对 service 层做越权断言。
"""

import io

import app.modules.knowledge.models  # noqa: F401  (注册 ORM 表)
import pytest
from app.core.config import settings
from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.security import create_access_token, decode_token
from app.modules.knowledge import service
from app.modules.knowledge.worker_tasks import parse_document_sync
from fastapi import UploadFile

YARD_A = "yard-sec-a"
YARD_B = "yard-sec-b"

AGRI_TEXT = "小麦赤霉病防治：扬花初期是防治适期，选用氰烯菌素等对路药剂。\n\n"


def _ctx_from_token(yard_id: str, user_id: str) -> YardContext:
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=["yard_admin"])
    payload = decode_token(token, expected_type="access")
    assert payload["yard_id"] == yard_id
    return YardContext(
        yard_id=payload["yard_id"],
        user_id=payload["sub"],
        roles=payload.get("roles", []),
        request_id="req-sec",
    )


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


@pytest.fixture()
def yard_a_doc(db_session, storage_tmp):
    """小院 A 上传并解析完成一份文档，返回 (ctx_a, document_id)。"""
    ctx_a = _ctx_from_token(YARD_A, "user-a")
    doc = service.upload_document(
        db_session,
        ctx_a,
        UploadFile(file=io.BytesIO(AGRI_TEXT.encode("utf-8")), filename="wheat.txt"),
        title="小麦赤霉病资料",
        request_id="req-sec-1",
    )
    parse_document_sync(db_session, doc.id)
    ready = service.get_document(db_session, ctx_a, doc.id)
    assert ready.status == "ready"
    return ctx_a, doc.id


def test_yard_b_cannot_read_yard_a_document(db_session, yard_a_doc):
    _, doc_id = yard_a_doc
    ctx_b = _ctx_from_token(YARD_B, "user-b")
    with pytest.raises(AppError) as exc_info:
        service.get_document(db_session, ctx_b, doc_id)
    assert exc_info.value.code == "DOCUMENT_NOT_FOUND"
    assert exc_info.value.http_status == 404


def test_yard_b_cannot_delete_yard_a_document(db_session, yard_a_doc):
    _, doc_id = yard_a_doc
    ctx_b = _ctx_from_token(YARD_B, "user-b")
    with pytest.raises(AppError) as exc_info:
        service.delete_document(db_session, ctx_b, doc_id)
    assert exc_info.value.code == "DOCUMENT_NOT_FOUND"
    # A 的文档未被删
    ctx_a, _ = yard_a_doc
    assert service.get_document(db_session, ctx_a, doc_id).status == "ready"


def test_yard_b_search_sees_nothing(db_session, yard_a_doc):
    ctx_b = _ctx_from_token(YARD_B, "user-b")
    hits = service.search(db_session, ctx_b, "小麦赤霉病", top_k=5)
    assert hits == []
    items, total = service.list_documents(db_session, ctx_b, 1, 20)
    assert total == 0
    assert items == []


def test_yard_a_still_has_full_access(db_session, yard_a_doc):
    ctx_a, doc_id = yard_a_doc
    hits = service.search(db_session, ctx_a, "小麦赤霉病防治", top_k=5)
    assert len(hits) > 0
    assert hits[0].document_id == doc_id
    items, total = service.list_documents(db_session, ctx_a, 1, 20)
    assert total == 1
