"""MCP 服务器 ORM（迁移 0016）。

安全三件套完整复刻 push/devices 连接器模式：
1. ``url_enc`` / ``headers_enc``（JSON：{头名: Fernet 密文}）——URL 与鉴权头
   **视同密钥**（复用 llm/supplier_crypto），任何接口响应只回掩码；
2. 出网前后双重 ``check_egress_url``（net_guard）：保存时校验 + 每次外呼前复检
   （防 DNS 重绑定）；
3. 连接级连续失败 5 次自动 ``enabled=false`` 熔断 + 审计（见 tool_bridge/service）。

``tool_cache_json`` 为连接测试成功时的工具快照::

    {"<mcp 工具名>": {"description": str, "input_schema": {...}, "cached_at": iso}}

运行时动态工具只按该快照解析（宁缺勿幻：没测过的工具模型看不到也调不到）。
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

#: 当前仅支持 MCP streamable-HTTP 传输（stdio/SSE 老传输一律拒绝，宁严勿松）
TRANSPORT_STREAMABLE_HTTP = "streamable_http"
VALID_TRANSPORTS = (TRANSPORT_STREAMABLE_HTTP,)


class McpServer(Base, TimestampMixin):
    __tablename__ = "mcp_servers"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_mcp_servers_tenant_name"),)

    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # MCP 端点 URL 的 Fernet 密文（可空：仅出现在先建后补的极端场景，创建接口必填）
    url_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 鉴权头逐值 Fernet 密文：{"Authorization": "<密文>", ...}
    headers_enc: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    transport: Mapped[str] = mapped_column(
        String(30), default=TRANSPORT_STREAMABLE_HTTP, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 连接测试成功时的工具快照（{name: {description, input_schema, cached_at}}）
    tool_cache_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
