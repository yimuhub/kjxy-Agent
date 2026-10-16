"""初始建表：全部 P0 核心表 + pgvector 扩展。

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-16
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _ts():
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    # pgvector 扩展仅 PostgreSQL 需要；SQLite/其他方言跳过（模型层已做跨方言回退）
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("username", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("email", sa.String(255), nullable=True, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(64), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        *_ts(),
    )

    op.create_table(
        "tenants",
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("province", sa.String(64), nullable=True),
        sa.Column("city", sa.String(64), nullable=True),
        sa.Column("scene_type", sa.String(64), nullable=True),
        sa.Column("config", sa.JSON, nullable=True),
        *_ts(),
    )

    op.create_table(
        "tenant_memberships",
        sa.Column(
            "tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False, index=True
        ),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_user"),
        *_ts(),
    )

    op.create_table(
        "tasks",
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("task_type", sa.String(100), nullable=False, index=True),
        sa.Column("status", sa.String(30), nullable=False, index=True, server_default="queued"),
        sa.Column("input_json", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("result_json", sa.JSON, nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("priority", sa.String(20), nullable=False, server_default="normal"),
        sa.Column("idempotency_key", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_tasks_tenant_idempotency"),
        *_ts(),
    )

    op.create_table(
        "task_events",
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False, index=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("message", sa.Text, nullable=False, server_default=""),
        sa.Column("progress", sa.Float, nullable=True),
        sa.Column("payload", sa.JSON, nullable=True),
        *_ts(),
    )

    op.create_table(
        "llm_calls",
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("task_id", sa.String(36), nullable=True, index=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=True),
        sa.Column("output_tokens", sa.Integer, nullable=True),
        sa.Column("estimated_cost", sa.Float, nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_category", sa.String(50), nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("request_id", sa.String(64), nullable=False, index=True),
        sa.Column("prompt_sha", sa.String(64), nullable=False),
        *_ts(),
    )

    op.create_table(
        "tool_definitions",
        sa.Column("code", sa.String(100), nullable=False, unique=True, index=True),
        sa.Column("version", sa.String(20), nullable=False, server_default="1.0.0"),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("input_schema", sa.JSON, nullable=False),
        sa.Column("output_schema", sa.JSON, nullable=True),
        sa.Column("required_permissions", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("risk_level", sa.String(30), nullable=False, server_default="read_only"),
        sa.Column("side_effect", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("timeout_seconds", sa.Integer, nullable=False, server_default="30"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        *_ts(),
    )

    op.create_table(
        "knowledge_documents",
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(255), nullable=False, unique=True),
        sa.Column("mime_type", sa.String(128), nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default="upload"),
        sa.Column("status", sa.String(16), nullable=False, index=True, server_default="queued"),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("uploaded_by", sa.String(36), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("page_count", sa.Integer, nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_ts(),
    )

    op.create_table(
        "knowledge_chunks",
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column(
            "document_id",
            sa.String(36),
            sa.ForeignKey("knowledge_documents.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("page_number", sa.Integer, nullable=True),
        sa.Column("title_path", sa.String(512), nullable=True),
        sa.Column("trust_level", sa.String(32), nullable=False, server_default="tenant_material"),
        sa.Column("visibility", sa.String(32), nullable=False, server_default="tenant"),
        sa.Column("metadata_json", sa.JSON, nullable=True),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Index("ix_knowledge_chunks_document_chunk", "document_id", "chunk_index"),
        *_ts(),
    )

    op.create_table(
        "evolution_feedback",
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("actor_id", sa.String(36), nullable=False),
        sa.Column("rating", sa.String(10), nullable=True),
        sa.Column("feedback_text", sa.Text, nullable=True),
        sa.Column("corrected_answer", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        *_ts(),
    )

    op.create_table(
        "evolution_knowledge_candidates",
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("source_feedback_id", sa.String(36), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False, server_default="user_feedback"),
        sa.Column(
            "status", sa.String(20), nullable=False, index=True, server_default="pending_review"
        ),
        sa.Column("reviewed_by", sa.String(36), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text, nullable=True),
        *_ts(),
    )

    op.create_table(
        "evolution_skills",
        sa.Column("tenant_id", sa.String(36), nullable=True),
        sa.Column("skill_code", sa.String(100), nullable=False, index=True),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("definition", sa.JSON, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, index=True, server_default="draft"),
        sa.Column("created_from", sa.String(100), nullable=True),
        sa.Column("approved_by", sa.String(36), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "skill_code", "version", name="uq_skill_tenant_code_version"
        ),
        *_ts(),
    )

    op.create_table(
        "policies",
        sa.Column("scope", sa.String(50), nullable=False, server_default="national"),
        sa.Column("province", sa.String(50), nullable=True, index=True),
        sa.Column("city", sa.String(50), nullable=True, index=True),
        sa.Column("department", sa.String(100), nullable=True),
        sa.Column("policy_name", sa.String(300), nullable=False, index=True),
        sa.Column("publish_department", sa.String(200), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("apply_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("apply_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("policy_type", sa.String(50), nullable=True, index=True),
        sa.Column("support_direction", sa.String(200), nullable=True),
        sa.Column("apply_conditions", sa.JSON, nullable=True),
        sa.Column("material_requirements", sa.JSON, nullable=True),
        sa.Column("content", sa.Text, nullable=True),
        sa.Column("source_url", sa.String(1000), nullable=True),
        sa.Column("source_hash", sa.String(64), nullable=True, index=True),
        sa.Column("first_crawled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, index=True, server_default="active"),
        sa.Column("review_status", sa.String(20), nullable=False, server_default="pending"),
        *_ts(),
    )

    op.create_table(
        "audit_logs",
        sa.Column("tenant_id", sa.String(36), nullable=True, index=True),
        sa.Column("actor_id", sa.String(36), nullable=True),
        sa.Column("action", sa.String(100), nullable=False, index=True),
        sa.Column("resource_type", sa.String(100), nullable=False),
        sa.Column("resource_id", sa.String(100), nullable=True),
        sa.Column("result", sa.String(20), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False, index=True),
        sa.Column("metadata", sa.JSON, nullable=True),
        *_ts(),
    )


def downgrade() -> None:
    for table in [
        "audit_logs",
        "policies",
        "evolution_skills",
        "evolution_knowledge_candidates",
        "evolution_feedback",
        "knowledge_chunks",
        "knowledge_documents",
        "tool_definitions",
        "llm_calls",
        "task_events",
        "tasks",
        "tenant_memberships",
        "tenants",
        "users",
    ]:
        op.drop_table(table)
