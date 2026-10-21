"""MCP 工具连接器：``mcp_servers``（W5a）。

Revision ID: 0016_mcp_servers
Revises: 0015_llm_traces_push
Create Date: 2026-10-21

背景：租户可自助接入 MCP 工具服务器（streamable-HTTP）。安全三件套复刻
push/devices 连接器：
1. ``url_enc`` / ``headers_enc``（JSON，{头名: Fernet 密文}）**视同密钥**——
   复用 llm/supplier_crypto，接口只回掩码；
2. 出网前后双重 check_egress_url（保存 + 每次外呼前复检）；
3. 连接级连续失败 5 次自动 ``enabled=false`` 熔断 + 审计。

``tool_cache_json`` 为连接测试成功时的工具快照
（{name: {description, input_schema, cached_at}}）——运行时 ``mcp__<id8>__<tool>``
动态工具只按快照解析，没测过的工具模型看不到也调不到。

可逆：downgrade 删表，不动任何既有业务数据行。
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_mcp_servers"
down_revision = "0015_llm_traces_push"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        # MCP 端点 URL 的 Fernet 密文（视同密钥，明文永不出库/出接口）
        sa.Column("url_enc", sa.Text(), nullable=True),
        # 鉴权头逐值 Fernet 密文（JSON: {"Authorization": "<密文>"}）
        sa.Column("headers_enc", sa.JSON(), nullable=True),
        sa.Column(
            "transport",
            sa.String(30),
            nullable=False,
            server_default="streamable_http",
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        # 连接测试成功时的工具快照（运行时动态工具唯一可见性来源）
        sa.Column("tool_cache_json", sa.JSON(), nullable=True),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(300), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.UniqueConstraint("tenant_id", "name", name="uq_mcp_servers_tenant_name"),
    )
    op.create_index("ix_mcp_servers_tenant_id", "mcp_servers", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_mcp_servers_tenant_id", table_name="mcp_servers")
    op.drop_table("mcp_servers")
