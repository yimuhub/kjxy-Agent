"""MockEmbeddingProvider 单元测试：确定性、维度、归一化。"""

import math

from app.core.config import settings
from app.modules.knowledge.embedding import MockEmbeddingProvider, get_embedding_provider


def test_deterministic_same_text_same_vector():
    p = MockEmbeddingProvider(dim=64)
    v1 = p.embed(["水稻分蘖期需要重点观察什么？"])[0]
    v2 = p.embed(["水稻分蘖期需要重点观察什么？"])[0]
    assert v1 == v2


def test_dimension_matches_settings():
    p = get_embedding_provider()
    vec = p.embed(["hello"])[0]
    assert len(vec) == settings.embedding_dim
    assert len(vec) == p.dim


def test_vectors_are_normalized():
    import pytest

    p = MockEmbeddingProvider(dim=128)
    for vec in p.embed(["a", "b", "c" * 100]):
        norm = math.sqrt(sum(v * v for v in vec))
        assert norm == pytest.approx(1.0)


def test_different_texts_different_vectors():
    p = MockEmbeddingProvider(dim=64)
    v1 = p.embed(["水稻"])[0]
    v2 = p.embed(["小麦"])[0]
    assert v1 != v2


def test_batch_order_and_count():
    p = MockEmbeddingProvider(dim=32)
    texts = ["t1", "t2", "t3"]
    vecs = p.embed(texts)
    assert len(vecs) == 3
    assert vecs[0] == p.embed(["t1"])[0]
