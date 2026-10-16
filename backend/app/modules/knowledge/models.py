"""知识库 ORM 模型。

knowledge_documents：原始文档登记；knowledge_chunks：切块与向量。
embedding 列用 TypeDecorator 实现跨方言：PostgreSQL 用 pgvector Vector(1536)，
SQLite/测试环境回退为 JSON(list[float])。
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.db.base import Base, TimestampMixin


class EmbeddingType(TypeDecorator):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        try:
            if dialect.name == "postgresql":
                from pgvector.sqlalchemy import Vector

                return dialect.type_descriptor(Vector(1536))
        except Exception:
            pass
        return dialect.type_descriptor(JSON())


class KnowledgeDocument(Base, TimestampMixin):
    __tablename__ = "knowledge_documents"

    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    # upload | expert_correction | seed
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="upload")
    # uploaded | queued | processing | ready | failed
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False, default="queued")
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

    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("knowledge_documents.id"), index=True, nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 可信级别: verified_official | expert_reviewed | tenant_material |
    # case_observation | model_candidate | unknown
    trust_level: Mapped[str] = mapped_column(String(32), nullable=False, default="tenant_material")
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="tenant")
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    embedding: Mapped[list | None] = mapped_column(EmbeddingType(), nullable=True)

    document: Mapped["KnowledgeDocument"] = relationship(
        "KnowledgeDocument", back_populates="chunks"
    )

    __table_args__ = (Index("ix_knowledge_chunks_document_chunk", "document_id", "chunk_index"),)
