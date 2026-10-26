"""混合检索单元测试：alpha=1.0 一致性、精确词召回、空查询 400、阈值过滤。"""

import pytest
from app.core.config import settings
from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.knowledge.embedding import MockEmbeddingProvider
from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument
from app.modules.knowledge.service import search

YARD = "t-hybrid-test"

DOCS = [
    (
        "小麦播种技术要点",
        "冬小麦适播期为10月5日至15日，播量每亩15至20公斤，播深3到5厘米。"
        "播前整地要求深耕25厘米以上，底肥施用磷酸二铵每亩20公斤。",
    ),
    (
        "水稻纹枯病防治方案",
        "水稻纹枯病主要危害叶鞘，病斑呈云纹状褐色，湿度大时向上扩展。"
        "防治适期为分蘖末期至孕穗期，亩用井冈霉素水剂100毫升兑水喷雾。",
    ),
    (
        "农产品溯源码使用指南",
        "农产品溯源码是记录产地、生产过程和检测信息的一物一码。"
        "消费者扫码可查看种植记录、农残检测报告和流转信息。",
    ),
]


@pytest.fixture()
def seeded_db(db_session):
    provider = MockEmbeddingProvider()
    for i, (title, content) in enumerate(DOCS):
        doc = KnowledgeDocument(
            yard_id=YARD,
            title=title,
            storage_key=f"hybrid-key-{i}",
            mime_type="text/plain",
            source="seed",
            status="ready",
            checksum=f"hybrid-checksum-{i}",
        )
        db_session.add(doc)
        db_session.flush()
        db_session.add(
            KnowledgeChunk(
                yard_id=YARD,
                document_id=doc.id,
                chunk_index=0,
                content=content,
                trust_level="yard_material",
                embedding=provider.embed([content])[0],
            )
        )
    db_session.commit()
    return db_session


@pytest.fixture()
def ctx():
    return YardContext(yard_id=YARD, user_id="u1", roles=["member"], request_id="test")


def test_alpha_1_equals_vector(seeded_db, ctx, monkeypatch):
    """alpha=1.0 时 hybrid 排序与分数与纯向量一致。"""
    monkeypatch.setattr(settings, "retrieval_alpha", 1.0)
    q = "小麦播种什么时候播量多少"
    vec_hits = search(seeded_db, ctx, q, top_k=5, mode="vector")
    hyb_hits = search(seeded_db, ctx, q, top_k=5, mode="hybrid")
    assert [h.chunk_id for h in vec_hits] == [h.chunk_id for h in hyb_hits]
    assert [h.score for h in vec_hits] == [h.score for h in hyb_hits]
    for h in hyb_hits:
        assert h.score_detail is not None
        assert h.score_detail["vector"] == h.score
        assert 0.0 <= h.score_detail["keyword"] <= 1.0


def test_hybrid_recalls_exact_title_words(seeded_db, ctx, monkeypatch):
    """精确词查询（文档原标题词）hybrid 能召回。"""
    monkeypatch.setattr(settings, "retrieval_alpha", 0.7)
    hits = search(seeded_db, ctx, "农产品溯源码怎么用", top_k=5, mode="hybrid")
    assert hits, "精确词查询应有命中"
    assert any("农产品溯源码使用指南" in h.title for h in hits)
    top = hits[0]
    assert top.score_detail is not None
    assert top.score_detail["keyword"] > 0.5  # 标题词几乎全被覆盖


def test_empty_query_400(seeded_db, ctx):
    """空 query 仍抛 400。"""
    with pytest.raises(AppError) as exc_info:
        search(seeded_db, ctx, "   ", top_k=5, mode="hybrid")
    assert exc_info.value.http_status == 400


def test_threshold_still_filters(seeded_db, ctx, monkeypatch):
    """阈值过滤在混合模式下仍生效：无关查询返回空。"""
    monkeypatch.setattr(settings, "retrieval_alpha", 0.7)
    hits = search(seeded_db, ctx, "量子计算机芯片光刻机半导体制造", top_k=5, mode="hybrid")
    assert hits == []


def test_invalid_mode_400(seeded_db, ctx):
    with pytest.raises(AppError) as exc_info:
        search(seeded_db, ctx, "小麦", top_k=5, mode="bogus")
    assert exc_info.value.http_status == 400


def test_default_mode_respects_settings(seeded_db, ctx, monkeypatch):
    """mode=None 时走 settings.retrieval_mode（默认 hybrid），向后兼容的显式路径仍可用。"""
    monkeypatch.setattr(settings, "retrieval_mode", "hybrid")
    monkeypatch.setattr(settings, "retrieval_alpha", 0.7)
    monkeypatch.setattr(settings, "retrieval_min_score", 0.0)  # 只验证路由与排序
    hits = search(seeded_db, ctx, "小麦播种技术要点")
    assert any("小麦播种技术要点" in h.title for h in hits)
    assert all(h.score_detail is not None for h in hits)
