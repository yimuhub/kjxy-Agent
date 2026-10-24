"""pgvector 向量索引：knowledge_chunks.embedding 建 HNSW 余弦距离索引。

Revision ID: 0008_pgvector_index
Revises: 0007_approval_ttl
Create Date: 2026-10-17

背景：service.search 的 pgvector 路径（settings.use_pgvector 且 PostgreSQL）
按 embedding 余弦距离排序取候选；没有向量索引时该排序是全表扫描，
知识量上来后检索耗时劣化。本迁移补 HNSW 索引（cosine 距离 <=> 操作符）。

仅 PostgreSQL 生效；SQLite（开发/测试）直接跳过——pgvector 扩展不可用。
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_pgvector_index"
down_revision = "0007_approval_ttl"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    # HNSW 需 pgvector >= 0.5；旧版本退回不建索引（检索仍正确，只是走全表扫描）
    bind = op.get_bind()
    ver = bind.execute(
        sa.text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    ).scalar()
    major, minor = (int(x) for x in str(ver).split(".")[:2]) if ver else (0, 0)
    if (major, minor) < (0, 5):
        op.execute("SELECT 1")  # no-op：低版本 pgvector 跳过 HNSW
        return
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_embedding_hnsw
        ON knowledge_chunks
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )


def downgrade() -> None:
    if not _is_postgres():
        return
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding_hnsw")
