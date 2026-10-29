"""模型绑定与调用归集（v3.25）

- 新增 ``llm_bindings``：把「谁用哪个模型」从单一 active 供应商扩展成三层绑定
  （全局默认 / 能力 purpose / 单 Agent），同层可按 priority 配多条兜底顺序。
- ``llm_calls`` 补 ``supplier_id`` 与 ``purpose``：成本可按供应商、按能力归集。

纯增量迁移，可完整 downgrade。

Revision ID: 0051_llm_bindings
Revises: 0050_device_analyses
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0051_llm_bindings"
down_revision = "0050_device_analyses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_bindings",
        sa.Column("id", sa.String(length=36), primary_key=True),
        # NULL=平台级绑定（所有小院可见）；非空=仅归属小院生效
        sa.Column("yard_id", sa.String(length=36), nullable=True),
        # global | purpose | agent
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        # global 恒为空串；purpose=能力码；agent=助手 id
        sa.Column("scope_key", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("supplier_id", sa.String(length=36), nullable=False),
        # 覆盖上游物理模型名（空=沿用供应商默认模型）
        sa.Column("model_name", sa.String(length=100), nullable=True),
        # 同层兜底顺序：越大越优先
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["supplier_id"], ["suppliers.id"], name="fk_llm_bindings_supplier", ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "yard_id", "scope_type", "scope_key", "supplier_id", name="uq_llm_bindings_scope"
        ),
    )
    op.create_index("ix_llm_bindings_yard_id", "llm_bindings", ["yard_id"])
    op.create_index("ix_llm_bindings_scope", "llm_bindings", ["yard_id", "scope_type", "scope_key"])

    op.add_column("llm_calls", sa.Column("supplier_id", sa.String(length=36), nullable=True))
    op.add_column("llm_calls", sa.Column("purpose", sa.String(length=32), nullable=True))
    op.create_index("ix_llm_calls_supplier_id", "llm_calls", ["supplier_id"])
    op.create_index("ix_llm_calls_purpose", "llm_calls", ["purpose"])


def downgrade() -> None:
    op.drop_index("ix_llm_calls_purpose", table_name="llm_calls")
    op.drop_index("ix_llm_calls_supplier_id", table_name="llm_calls")
    op.drop_column("llm_calls", "purpose")
    op.drop_column("llm_calls", "supplier_id")
    op.drop_index("ix_llm_bindings_scope", table_name="llm_bindings")
    op.drop_index("ix_llm_bindings_yard_id", table_name="llm_bindings")
    op.drop_table("llm_bindings")
