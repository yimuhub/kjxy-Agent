"""演示经营线资料：增长演示文档幂等写入（v3.26 D-1.6）。"""

from __future__ import annotations

from app.db.seed import (
    DEMO_GROWTH_DOC_TEXT,
    DEMO_GROWTH_DOC_TITLE,
    _ensure_demo_growth_doc,
)
from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument
from app.modules.yards.repository import create_yard


def test_growth_demo_doc_is_idempotent(db_session):
    yard = create_yard(db_session, name="增长演示小院")
    db_session.flush()

    assert _ensure_demo_growth_doc(db_session, yard_id=yard.id, actor_id="u-seed") is True
    assert _ensure_demo_growth_doc(db_session, yard_id=yard.id, actor_id="u-seed") is False

    docs = db_session.query(KnowledgeDocument).filter_by(yard_id=yard.id).all()
    assert len(docs) == 1
    doc = docs[0]
    assert doc.title == DEMO_GROWTH_DOC_TITLE
    assert doc.status == "ready"
    assert doc.uploaded_by == "u-seed"

    chunks = db_session.query(KnowledgeChunk).filter_by(document_id=doc.id).all()
    assert len(chunks) >= 1
    assert all(chunk.embedding for chunk in chunks)
    assert all(chunk.visibility == "yard" for chunk in chunks)


def test_growth_demo_doc_keeps_honest_wording():
    """演示资料不能把未证实的认证/功效写成事实。"""
    assert "演示" in DEMO_GROWTH_DOC_TEXT
    assert "不是真实检测报告" in DEMO_GROWTH_DOC_TEXT
    assert "必须先取得检测报告" in DEMO_GROWTH_DOC_TEXT
