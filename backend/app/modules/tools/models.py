"""工具元数据模型。

ToolDefinition 只存工具的"契约"（code/schema/权限/风险等级），不存实现；
真正的可执行函数由各模块通过 ToolRegistry.register 注册。

ToolApproval 记录高风险工具（risk_level=external_side_effect）的人工审批：
调用被拦截时自动创建 pending 行，审批通过/驳回后流转状态。
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ToolDefinition(Base, TimestampMixin):
    __tablename__ = "tool_definitions"

    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0.0")
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    input_schema_json: Mapped[dict] = mapped_column("input_schema", JSON, nullable=False)
    output_schema_json: Mapped[dict | None] = mapped_column("output_schema", JSON, nullable=True)
    required_permissions_json: Mapped[list] = mapped_column(
        "required_permissions", JSON, nullable=False, default=list
    )
    risk_level: Mapped[str] = mapped_column(
        String(30), nullable=False, default="read_only"
    )  # read_only|draft|write_reversible|external_side_effect
    side_effect: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_REJECTED = "rejected"
APPROVAL_CANCELLED = "cancelled"

APPROVAL_STATUSES = frozenset(
    {APPROVAL_PENDING, APPROVAL_APPROVED, APPROVAL_REJECTED, APPROVAL_CANCELLED}
)


class ToolApproval(Base, TimestampMixin):
    """高风险工具调用的人工审批单。

    - 高风险工具（external_side_effect）被拦截时自动创建 pending 行
     （同租户+同工具+同参数去重）。
    - 审批通过后，调用方凭 approved 审批单重试，工具方可执行；
      审批单状态流转 pending→approved/rejected/cancelled。
    - task_id 关联触发该审批的任务（可空：工具可被任务外直接调用）。
    """

    __tablename__ = "tool_approvals"

    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    tool_code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    params_json: Mapped[dict] = mapped_column("params", JSON, nullable=False, default=dict)
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=APPROVAL_PENDING, index=True
    )  # pending|approved|rejected|cancelled
    requested_by: Mapped[str] = mapped_column(String(36), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 审批有效期（批准时按 settings.tool_approval_ttl_hours 计算；0=永久）
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: 该审批已被放行的次数（受 settings.tool_approval_max_uses 上限约束）
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
