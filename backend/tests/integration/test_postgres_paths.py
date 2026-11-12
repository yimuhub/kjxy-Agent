"""PostgreSQL + pgvector 真实链路测试（审查 D-01 / D-05 / D-22 的根因护栏）。

为什么必须有这个文件
-------------------
本轮审查的三个 P0 缺陷**只在 PostgreSQL 上复现**，而 CI 此前所有单测都跑在
SQLite 内存库上——于是「SQLite 全绿、生产库 500」：

- **D-01**：pgvector 的 ``Vector`` 列读回是 numpy.ndarray，业务层一旦写
  ``if not vec:`` 就抛 ``ValueError: truth value of an array ... is ambiguous``；
  SQLite 的 JSON 回退读回是 list，永远不触发。
- **D-05 / D-22**：``GROUP BY coalesce(purpose, :param)`` 在 SQLite 能跑，
  PostgreSQL 直接报 "column must appear in the GROUP BY clause" → 指挥官总览 500。
- **检索排序**：pgvector 的 ``<=>`` 距离算子路径与 SQLite 全量扫描路径是两条
  完全不同的代码分支，扫描路径绿灯不代表向量路径绿灯。

因此这里把这三条链路钉在真实 PostgreSQL 上跑：CI 新增
``postgres-integration`` job（pgvector/pg17 + alembic 迁移 + 本文件）。

本地跑法（不依赖 CI）::

    docker run -d --name kjxy-pg -p 55432:5432 \\
      -e POSTGRES_USER=app_user -e POSTGRES_PASSWORD=ci-password \\
      -e POSTGRES_DB=techyard pgvector/pgvector:pg17
    cd backend && DATABASE_URL=postgresql+psycopg://app_user:ci-password@localhost:55432/techyard \\
      uv run alembic upgrade head
    uv run pytest tests/integration/test_postgres_paths.py -v
"""

from __future__ import annotations

import uuid

import pytest
from app.core.deps import YardContext
from app.db.base import normalize_vector
from app.modules.commander import metrics as commander_metrics
from app.modules.knowledge import service as knowledge_service
from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument
from app.modules.llm.models import LLMCall
from app.modules.memory import service as memory_service
from app.modules.memory.models import (
    KIND_FACT,
    SCOPE_YARD,
    SOURCE_MANUAL,
    STATUS_ACTIVE,
    VISIBILITY_YARD,
    AgentMemory,
)

pytestmark = pytest.mark.postgres

YARD_ID = "pg-yard-0001"
USER_ID = "pg-user-0001"


@pytest.fixture()
def ctx() -> YardContext:
    return YardContext(
        yard_id=YARD_ID,
        user_id=USER_ID,
        roles=["yard_owner"],
        request_id="pg-test",
    )


def _embedding_for(text: str) -> list[float]:
    """用 mock 提供方算真实向量（与生产同一条链路：哈希词袋 + L2 归一化）。"""
    from app.modules.knowledge.embedding import get_embedding_provider

    provider = get_embedding_provider(None, None)
    return normalize_vector(provider.embed([text])[0]) or []


def _seed_document(db, *, content: str, embedding: list[float]) -> None:
    doc_id = str(uuid.uuid4())
    db.add(
        KnowledgeDocument(
            id=doc_id,
            yard_id=YARD_ID,
            title="葡萄疏果技术要点",
            storage_key=f"pg/{doc_id}.pdf",
            mime_type="application/pdf",
            status="ready",
            checksum="pg-checksum",
            uploaded_by=USER_ID,
        )
    )
    db.add(
        KnowledgeChunk(
            id=str(uuid.uuid4()),
            yard_id=YARD_ID,
            document_id=doc_id,
            chunk_index=0,
            content=content,
            trust_level="yard_material",
            visibility="yard",
            embedding=embedding,
        )
    )
    db.commit()


def _seed_memory(db, *, content: str, embedding: list[float]) -> None:
    db.add(
        AgentMemory(
            id=str(uuid.uuid4()),
            yard_id=YARD_ID,
            scope_type=SCOPE_YARD,
            scope_key="",
            kind=KIND_FACT,
            title="葡萄疏果期管理",
            content=content,
            importance=0.8,
            confidence=0.9,
            source_type=SOURCE_MANUAL,
            source_id=str(uuid.uuid4()),
            visibility=VISIBILITY_YARD,
            embedding=embedding,
            status=STATUS_ACTIVE,
            created_by=USER_ID,
        )
    )
    db.commit()


# ---------------------------------------------------------------------------
# 1. 知识检索：pgvector 写入 → 读回 → 距离排序
# ---------------------------------------------------------------------------


def test_pgvector_knowledge_search_hits_expected_chunk(pg_db_session, ctx):
    """D-01：读回的 ndarray 真值判断曾在生产库直接 500。"""
    content = "葡萄疏果期要控制每穗果粒数，通常每穗保留 6 到 8 粒，并及时疏除弱果。"
    _seed_document(pg_db_session, content=content, embedding=_embedding_for(content))

    hits = knowledge_service.search(pg_db_session, ctx, "葡萄疏果每穗保留几粒")

    assert hits, "真实向量库里应当检索到刚写入的 chunk"
    assert "疏果" in hits[0].quote
    assert hits[0].score > 0


def test_pgvector_knowledge_search_reads_embedding_as_list(pg_db_session, ctx):
    """向量列读回必须是 list[float]（业务层不做方言判断）。"""
    content = "灌溉决策：水稻分蘖期浅水勤灌，够苗后适时晒田。"
    _seed_document(pg_db_session, content=content, embedding=_embedding_for(content))

    chunk = pg_db_session.query(KnowledgeChunk).first()
    assert chunk is not None
    assert isinstance(chunk.embedding, list)
    assert all(isinstance(v, float) for v in chunk.embedding)


def test_pgvector_search_respects_ready_status(pg_db_session, ctx):
    """未就绪的文档不参与检索（与 SQLite 路径同口径）。"""
    content = "尚未解析的文档内容不应被检索命中。"
    doc_id = str(uuid.uuid4())
    pg_db_session.add(
        KnowledgeDocument(
            id=doc_id,
            yard_id=YARD_ID,
            title="待解析",
            storage_key=f"pg/{doc_id}.pdf",
            mime_type="application/pdf",
            status="queued",
            checksum="pg-checksum-2",
        )
    )
    pg_db_session.add(
        KnowledgeChunk(
            id=str(uuid.uuid4()),
            yard_id=YARD_ID,
            document_id=doc_id,
            chunk_index=0,
            content=content,
            embedding=_embedding_for(content),
        )
    )
    pg_db_session.commit()

    assert knowledge_service.search(pg_db_session, ctx, "未解析的文档") == []


# ---------------------------------------------------------------------------
# 2. 记忆检索：同样是 pgvector 列 + numpy 读回
# ---------------------------------------------------------------------------


def test_pgvector_memory_retrieve_hits_expected_row(pg_db_session, ctx):
    content = "小院的葡萄品种是阳光玫瑰，疏果期在坐果后 15 天左右。"
    _seed_memory(pg_db_session, content=content, embedding=_embedding_for(content))

    hits = memory_service.retrieve(pg_db_session, ctx, "葡萄什么时候疏果", top_k=3)

    assert hits, "记忆检索应命中刚写入的事实"
    assert "阳光玫瑰" in hits[0].content


def test_pgvector_memory_embedding_is_list(pg_db_session, ctx):
    content = "小院的水稻品种是南粳 9108。"
    _seed_memory(pg_db_session, content=content, embedding=_embedding_for(content))

    row = pg_db_session.query(AgentMemory).first()
    assert row is not None
    assert isinstance(row.embedding, list)


# ---------------------------------------------------------------------------
# 3. 指挥官总览：GROUP BY 常量必须内联成 SQL 字面量
# ---------------------------------------------------------------------------


def _seed_llm_call(db, *, purpose: str | None, model: str, tokens: int) -> None:
    db.add(
        LLMCall(
            id=str(uuid.uuid4()),
            yard_id=YARD_ID,
            task_id=None,
            provider="fake",
            model=model,
            input_tokens=tokens,
            output_tokens=tokens // 2,
            estimated_cost=0.01,
            status="success",
            latency_ms=120,
            request_id="pg-req-1",
            prompt_sha="0" * 64,
            purpose=purpose,
        )
    )
    db.commit()


def test_commander_cost_stats_group_by_works_on_postgres(pg_db_session):
    """D-05/D-22：GROUP BY 里的常量若渲染成绑定参数，PG 直接报 500。"""
    _seed_llm_call(pg_db_session, purpose=None, model="agnes-3.0-flash", tokens=1000)
    _seed_llm_call(pg_db_session, purpose="chat", model="agnes-3.0-flash", tokens=2000)

    stats = commander_metrics.cost_stats(pg_db_session, YARD_ID, days=30)

    assert stats["total"]["calls"] == 2
    assert stats["total"]["tokens"] == 1500 + 3000
    # purpose 为空的调用归到 'platform'（coalesce 的字面量口径）
    by_purpose = {row["purpose"]: row for row in stats["by_purpose"]}
    assert "platform" in by_purpose
    assert by_purpose["platform"]["calls"] == 1
    assert by_purpose["chat"]["calls"] == 1


def test_commander_stats_runs_on_postgres(pg_db_session):
    """指挥官运行统计在真实 PG 上可跑（无运行记录时应返回零值而不是抛错）。"""
    stats = commander_metrics.stats(pg_db_session, YARD_ID, days=30)
    assert stats["runs"]["total"] == 0
    assert stats["plan"]["success_rate"] == 0
