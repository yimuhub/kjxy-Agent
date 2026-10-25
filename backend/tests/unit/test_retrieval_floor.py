"""知识与进化闭环回归（子任务 E）。

覆盖：
1. 检索保底（A9）：关键词完整覆盖但向量分低时，最终得分保底 min_score+0.01，仍能命中；
2. 切块信任级映射（S4 关联）：worker 按文档 source 映射 trust_level；
3. POST /knowledge/documents/{id}/reparse 端点（追加任务 A）；
4. POST /knowledge/candidates/batch-review 端点与 S1 入库失败显式化（追加任务 B）。
   （3/4 的集成测试按任务约束放入本子任务已授权的测试文件中。）
"""

import io

import app.modules.evolution.models  # noqa: F401
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
from app.modules.evolution.models import KnowledgeCandidate
from app.modules.knowledge import service
from app.modules.knowledge.embedding import MockEmbeddingProvider
from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument
from app.modules.knowledge.router import router as knowledge_router
from app.modules.knowledge.worker_tasks import _trust_level_for_source, parse_document_sync
from fastapi import FastAPI, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

TENANT_FLOOR = "t-floor-test"

# 填充文本：把 chunk 撑长，使短查询（其子串）的 mock 向量分被显著稀释
_PADDING = "田间水肥管理与病虫害监测记录，农户需按时巡查并做好台账登记。" * 40


def _seed_doc(db, title: str, content: str, tenant_id: str = TENANT_FLOOR, source: str = "seed"):
    provider = MockEmbeddingProvider()
    doc = KnowledgeDocument(
        tenant_id=tenant_id,
        title=title,
        storage_key=f"floor-{title}-{id(content)}",
        mime_type="text/plain",
        source=source,
        status="ready",
        checksum="floor-checksum",
    )
    db.add(doc)
    db.flush()
    db.add(
        KnowledgeChunk(
            tenant_id=tenant_id,
            document_id=doc.id,
            chunk_index=0,
            content=content,
            trust_level="tenant_material",
            embedding=provider.embed([content])[0],
        )
    )
    db.commit()
    return doc


@pytest.fixture()
def floor_db(db_session):
    content = "晒田控旺期间应控制无效分蘖，" + _PADDING
    _seed_doc(db_session, "晒田管理补充资料", content)
    _seed_doc(
        db_session,
        "农产品溯源码指南",
        "农产品溯源码是一物一码，扫码可查农残检测报告与流转信息。",
    )
    return db_session


@pytest.fixture()
def ctx():
    return TenantContext(tenant_id=TENANT_FLOOR, user_id="u1", roles=["operator"], request_id="t")


def test_coverage_floor_recalls_low_vector_hit(floor_db, ctx, monkeypatch):
    """关键词满覆盖（问题 token 全部出现在 chunk）但向量分低 → 保底后仍能命中。"""
    monkeypatch.setattr(settings, "retrieval_min_score", 0.4)
    monkeypatch.setattr(settings, "retrieval_alpha", 0.7)
    query = "晒田控旺"  # chunk 的子串：bigram 全覆盖；chunk 很长 → 向量分被稀释

    hits = service.search(floor_db, ctx, query, top_k=5, mode="hybrid")
    target = [h for h in hits if "晒田管理" in h.title]
    assert target, "关键词完整覆盖的 chunk 应被保底召回"
    hit = target[0]
    # 保底值 = min_score + 0.01
    assert hit.score == pytest.approx(0.41)
    # 证明确实发生了保底：按 score_detail 复算的融合分低于阈值
    assert hit.score_detail is not None
    raw = 0.7 * hit.score_detail["vector"] + 0.3 * hit.score_detail["keyword"]
    assert raw < 0.4, f"融合分应低于阈值才能证明保底生效，实际 raw={raw}"
    assert hit.score_detail["keyword"] == pytest.approx(1.0)


def test_coverage_floor_only_hybrid(floor_db, ctx, monkeypatch):
    """纯向量模式无关键词通道，不触发保底（低向量分照旧被阈值过滤）。"""
    monkeypatch.setattr(settings, "retrieval_min_score", 0.4)
    hits = service.search(floor_db, ctx, "晒田控旺", top_k=5, mode="vector")
    assert all("晒田管理" not in h.title for h in hits)


def test_partial_coverage_not_floored(floor_db, ctx, monkeypatch):
    """非满覆盖（部分 token 未出现）不享受保底：无关长查询仍为空。"""
    monkeypatch.setattr(settings, "retrieval_min_score", 0.4)
    hits = service.search(floor_db, ctx, "量子计算机光刻工艺参数", top_k=5, mode="hybrid")
    assert hits == []


# ---------------------------------------------------------------- 信任级映射
def test_trust_level_mapping_by_source():
    assert _trust_level_for_source("expert_correction") == "expert_reviewed"
    assert _trust_level_for_source("official") == "verified_official"
    assert _trust_level_for_source("政策文件库") == "verified_official"
    assert _trust_level_for_source("上传资料-官方发布") == "verified_official"
    assert _trust_level_for_source("upload") == "tenant_material"
    assert _trust_level_for_source("seed") == "tenant_material"
    assert _trust_level_for_source(None) == "tenant_material"


def test_parse_applies_trust_level_by_source(db_session, storage_tmp):
    """端到端：expert_correction 文档解析出的 chunk 信任级为 expert_reviewed。"""
    ctx = TenantContext(
        tenant_id="tenant-trust", user_id="u-trust", roles=["tenant_admin"], request_id="req"
    )
    doc = service.upload_document(
        db_session,
        ctx,
        UploadFile(file=io.BytesIO("专家更正：拌种药种比为 1:50。".encode()), filename="e.md"),
        title="专家纠错",
        request_id="req",
    )
    row = db_session.get(KnowledgeDocument, doc.id)
    row.source = "expert_correction"
    db_session.commit()
    parse_document_sync(db_session, doc.id)
    chunks = db_session.query(KnowledgeChunk).filter(KnowledgeChunk.document_id == doc.id).all()
    assert chunks and all(c.trust_level == "expert_reviewed" for c in chunks)


# ---------------------------------------------------------------- API 装配
def _ctx_admin(tenant_id: str, roles: list[str] | None = None) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        user_id=f"u-{tenant_id}",
        roles=roles or ["tenant_admin"],
        request_id="req-api",
    )


def _build_app(db_session, ctx: TenantContext) -> FastAPI:
    """knowledge + evolution 路由的最小可测装配（同 test_knowledge_flow 模式）。"""
    from app.modules.evolution.router import router as evolution_router

    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
    app.include_router(knowledge_router, prefix="/api/v1")
    app.include_router(evolution_router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = lambda: db_session
    app.dependency_overrides[get_tenant_context] = lambda: ctx
    return app


@pytest.fixture()
def storage_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "local_storage_path", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def celery_memory_transport(monkeypatch):
    """memory broker 投递解析任务，避免无 Redis 时连接重试风暴。"""
    from app.workers.celery_app import celery_app

    monkeypatch.setattr(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setattr(celery_app.conf, "result_backend", "cache+memory://")


def _upload(db, ctx, filename: str, data: bytes, title: str):
    return service.upload_document(
        db, ctx, UploadFile(file=io.BytesIO(data), filename=filename), title=title, request_id="r"
    )


# ---------------------------------------------------------------- reparse 端点（追加任务 A）
def test_reparse_failed_document_flow(db_session, storage_tmp):
    ctx = _ctx_admin("tenant-rp")
    # 制造 failed 文档：坏 PDF → 解析失败
    doc = _upload(db_session, ctx, "broken.pdf", b"not a pdf at all", "坏文档")
    parse_document_sync(db_session, doc.id)
    assert service.get_document(db_session, ctx, doc.id).status == "failed"

    app = _build_app(db_session, ctx)
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post(f"/api/v1/knowledge/documents/{doc.id}/reparse")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["data"]["document_id"] == doc.id
        assert body["data"]["status"] == "queued"
        assert "task_id" in body["data"]
        assert "request_id" in body
    # 状态已重置为 queued 且 error_message 清空（broker 在测试中不消费，保持 queued 合理）
    refreshed = service.get_document(db_session, ctx, doc.id)
    assert refreshed.status == "queued"
    assert refreshed.error_message is None


def test_reparse_guards(db_session, storage_tmp):
    ctx = _ctx_admin("tenant-rp2")
    # ready 文档不可 reparse → 409
    doc = _upload(db_session, ctx, "ok.txt", "正常文本内容，可以直接解析。".encode(), "好文档")
    parse_document_sync(db_session, doc.id)
    assert service.get_document(db_session, ctx, doc.id).status == "ready"

    app = _build_app(db_session, ctx)
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post(f"/api/v1/knowledge/documents/{doc.id}/reparse")
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "DOCUMENT_NOT_REPARSABLE"

        r = client.post("/api/v1/knowledge/documents/no-such-id/reparse")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"

    # 跨租户 → 404（不泄露存在性）
    other = _ctx_admin("tenant-rp-other")
    app2 = _build_app(db_session, other)
    with TestClient(app2, raise_server_exceptions=False) as client:
        r = client.post(f"/api/v1/knowledge/documents/{doc.id}/reparse")
        assert r.status_code == 404

    # 无 knowledge:write 权限（viewer）→ 403
    viewer = _ctx_admin("tenant-rp2", roles=["viewer"])
    app3 = _build_app(db_session, viewer)
    with TestClient(app3, raise_server_exceptions=False) as client:
        r = client.post(f"/api/v1/knowledge/documents/{doc.id}/reparse")
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "FORBIDDEN"


# ---------------------------------------------------------------- batch-review 端点（追加任务 B）
def _make_candidates(db, ctx, n: int) -> list[str]:
    from app.modules.evolution import service as evo_service

    for i in range(n):
        evo_service.submit_feedback(
            db,
            ctx,
            feedback_text=f"纠错反馈{i}",
            corrected_answer=f"更正答案内容{i}：分蘖肥应早施。",
            request_id="req",
        )
        db.commit()
    rows = (
        db.query(KnowledgeCandidate)
        .filter_by(tenant_id=ctx.tenant_id)
        .order_by(KnowledgeCandidate.created_at)
        .all()
    )
    ids = [r.id for r in rows]
    assert len(ids) == n
    return ids


def test_batch_review_endpoint_flow(db_session, storage_tmp):
    ctx = _ctx_admin("tenant-batch")
    c1, c2, c3 = _make_candidates(db_session, ctx, 3)

    app = _build_app(db_session, ctx)
    with TestClient(app, raise_server_exceptions=False) as client:
        # approve 两条 + 一个不存在的 ID：单条失败不影响其余
        r = client.post(
            "/api/v1/knowledge/candidates/batch-review",
            json={"ids": [c1, c2, "no-such-id"], "action": "approve", "review_note": "批量通过"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        data = body["data"]
        assert set(data.keys()) == {"approved", "rejected", "failed"}
        assert sorted(data["approved"]) == sorted([c1, c2])
        assert data["rejected"] == []
        assert len(data["failed"]) == 1
        assert data["failed"][0]["id"] == "no-such-id"
        assert data["failed"][0]["error_code"] == "CANDIDATE_NOT_FOUND"
        assert data["failed"][0]["error_message"]
        assert "request_id" in body

        # reject 剩余一条；重复 approve 已审核条目 → 逐条计入 failed
        r = client.post(
            "/api/v1/knowledge/candidates/batch-review",
            json={"ids": [c3, c1], "action": "reject"},
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["rejected"] == [c3]
        assert data["failed"][0]["id"] == c1
        assert data["failed"][0]["error_code"] == "INVALID_REVIEW_ACTION"

        # ids 为空 → 校验拒绝（400，统一 INVALID_REQUEST 信封）
        r = client.post(
            "/api/v1/knowledge/candidates/batch-review", json={"ids": [], "action": "approve"}
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "INVALID_REQUEST"

    # approve 的两条已真正入库（走 S1 正常路径）：knowledge 侧出现 expert_correction 文档
    docs = (
        db_session.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.tenant_id == ctx.tenant_id,
            KnowledgeDocument.source == "expert_correction",
        )
        .all()
    )
    assert len(docs) == 2


def test_batch_review_requires_permission(db_session, storage_tmp):
    from app.modules.evolution import service as evo_service

    ctx = _ctx_admin("tenant-batch-perm")
    fb = evo_service.submit_feedback(
        db_session, ctx, corrected_answer="纠错内容占位测试文本", request_id="r"
    )
    db_session.commit()
    rows = db_session.query(evo_service.KnowledgeCandidate).filter_by(tenant_id=ctx.tenant_id).all()
    cid = rows[0].id

    viewer = TenantContext(tenant_id=ctx.tenant_id, user_id="v", roles=["viewer"], request_id="req")
    app = _build_app(db_session, viewer)
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post(
            "/api/v1/knowledge/candidates/batch-review", json={"ids": [cid], "action": "approve"}
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "FORBIDDEN"
    assert fb is not None


# ---------------------------------------------------------------- S1：单条审核入库失败显式化
def test_review_single_exposes_ingest_failure(db_session, storage_tmp, monkeypatch):
    from app.modules.evolution import service as evo_service

    ctx = _ctx_admin("tenant-s1")
    evo_service.submit_feedback(
        db_session, ctx, corrected_answer="更正：晒田应看苗情。", request_id="r"
    )
    db_session.commit()
    cand = (
        db_session.query(evo_service.KnowledgeCandidate).filter_by(tenant_id=ctx.tenant_id).first()
    )

    # 模拟知识库写入失败（如存储盘故障）
    def _boom(*args, **kwargs):
        raise RuntimeError("storage disk exploded")

    monkeypatch.setattr("app.modules.knowledge.service.upload_document", _boom)

    app = _build_app(db_session, ctx)
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post(
            f"/api/v1/knowledge/candidates/{cand.id}/review",
            json={"action": "approve", "note": "n"},
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        # 审核状态照常提交，但响应不再伪装纯成功
        assert data["status"] == "approved"
        assert data["ingest_ok"] is False
        assert "storage disk exploded" in (data["ingest_error"] or "")

    # 失败原因持久化在审计 metadata（candidate 表无 ingest 字段，按规范走审计留痕）
    from app.modules.audit.models import AuditLog

    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.tenant_id == ctx.tenant_id,
            AuditLog.action == "knowledge.ingest_failed",
        )
        .first()
    )
    assert audit is not None
    assert audit.metadata_json and "storage disk exploded" in str(audit.metadata_json)
    # 审核结论本身没有被回滚
    refreshed = db_session.query(evo_service.KnowledgeCandidate).filter_by(id=cand.id).first()
    assert refreshed is not None and refreshed.status == "approved"
