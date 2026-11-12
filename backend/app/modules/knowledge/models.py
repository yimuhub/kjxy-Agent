"""知识库 ORM 模型。

knowledge_documents：原始文档登记；knowledge_chunks：切块与向量。
embedding 列用 TypeDecorator 实现跨方言：PostgreSQL 用 pgvector Vector(1536)，
SQLite/测试环境回退为 JSON(list[float])。
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.db.base import Base, TimestampMixin, normalize_vector


class EmbeddingType(TypeDecorator):
    """跨方言向量列。

    - PostgreSQL：pgvector ``Vector(dim)``，读回是 numpy.ndarray
    - SQLite / 测试：JSON(list[float])

    读回一律经 :func:`normalize_vector` 收敛为 ``list[float]``，
    业务层不再需要区分方言（避免 ndarray 真值判断抛 ValueError）。
    """

    impl = JSON
    cache_ok = True

    def __init__(self, dim: int | None = None, *args, **kwargs) -> None:
        # 维度默认取配置；改维度需要配套迁移，历史库保持 1536 不受影响。
        from app.core.config import settings

        self.dim = int(dim or settings.embedding_dim)
        super().__init__(*args, **kwargs)

    def load_dialect_impl(self, dialect):
        try:
            if dialect.name == "postgresql":
                from pgvector.sqlalchemy import Vector

                return dialect.type_descriptor(Vector(self.dim))
        except Exception:
            pass
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):
        # 统一以 list[float] 绑定：pgvector 内部再转 numpy，避免 ndarray 直传。
        return normalize_vector(value)

    def process_result_value(self, value, dialect):
        # 关键修复：pgvector 读回的 ndarray → list[float]。
        return normalize_vector(value)


class KnowledgeDocument(Base, TimestampMixin):
    __tablename__ = "knowledge_documents"

    yard_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    # upload | expert_correction | seed
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="upload")
    # uploaded | queued | processing | ready | failed
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False, default="queued")
    #: 解析代数（attempt/generation）：每次 CAS 抢占（queued→processing）自增 1。
    #: 看门狗僵尸回收与人工重解析会把 processing 重置回 queued，此时上一个
    #: 执行者可能还活着——它收尾/失败标记必须带
    #: ``WHERE parse_attempt = 自己抢到的代数``，被超越者静默退出，
    #: 绝不与新执行者并发写同一份文档的切块（2026-11-13 二次深审修正）。
    parse_attempt: Mapped[int] = mapped_column(default=0, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        "KnowledgeChunk", back_populates="document", cascade="all, delete-orphan"
    )


class KnowledgeChunk(Base, TimestampMixin):
    __tablename__ = "knowledge_chunks"

    yard_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("knowledge_documents.id"), index=True, nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 可信级别: verified_official | expert_reviewed | yard_material |
    # case_observation | model_candidate | unknown
    trust_level: Mapped[str] = mapped_column(String(32), nullable=False, default="yard_material")
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="yard")
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    #: 向量列；``EmbeddingType`` 读回已收敛为 ``list[float]``（跨方言统一）。
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingType(), nullable=True)

    document: Mapped["KnowledgeDocument"] = relationship(
        "KnowledgeDocument", back_populates="chunks"
    )

    __table_args__ = (Index("ix_knowledge_chunks_document_chunk", "document_id", "chunk_index"),)
