"""可用工具目录扩展（v3.25）

``tool_definitions`` 此前只描述内置工具（实现在内存注册表里）。本轮增加：

- ``source``：builtin（内置）/ custom（用户手填或一句话生成）；MCP 工具不入库，
  由 ``mcp_servers.tool_cache_json`` 快照动态并入列表。
- ``impl_kind``/``impl_json``：自定义工具的执行方式（prompt=复用小院模型推理，
  http=受出网管控的 JSON 调用），缺省 registry 表示走内置实现。
- ``yard_id``/``created_by``：自定义工具归属小院与创建人，内置行保持 NULL=全局。

纯增量迁移，可完整 downgrade。

Revision ID: 0052_tool_catalog
Revises: 0051_llm_bindings
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0052_tool_catalog"
down_revision = "0051_llm_bindings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tool_definitions",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="builtin"),
    )
    op.add_column(
        "tool_definitions",
        sa.Column("impl_kind", sa.String(length=16), nullable=False, server_default="registry"),
    )
    op.add_column("tool_definitions", sa.Column("impl_json", sa.JSON(), nullable=True))
    op.add_column("tool_definitions", sa.Column("yard_id", sa.String(length=36), nullable=True))
    op.add_column("tool_definitions", sa.Column("created_by", sa.String(length=36), nullable=True))
    op.create_index("ix_tool_definitions_source", "tool_definitions", ["source"])
    op.create_index("ix_tool_definitions_yard_id", "tool_definitions", ["yard_id"])


def downgrade() -> None:
    op.drop_index("ix_tool_definitions_yard_id", table_name="tool_definitions")
    op.drop_index("ix_tool_definitions_source", table_name="tool_definitions")
    op.drop_column("tool_definitions", "created_by")
    op.drop_column("tool_definitions", "yard_id")
    op.drop_column("tool_definitions", "impl_json")
    op.drop_column("tool_definitions", "impl_kind")
    op.drop_column("tool_definitions", "source")
