"""Agent 运行步骤 checkpoint（迁移 0013）：工具循环的可恢复状态。

一行 ``agent_run_steps`` = 运行链路中的一次原子动作：

- ``llm_call``：一次模型调用。input_json 携带 base 提示词与运行入参
  （首个 llm 步必须携带，断点续跑据此精确重建会话）；output_json 携带
  content / tool_calls / token 数。
- ``tool_call``：一次工具执行（经 tool_registry.execute 全通道）。input_json
  携带 tool_call_id 与解析后的参数；output_json 为工具结果原文（失败为
  {"error": {...}} 且 status=error）。
- ``final``：运行收敛终态标记（SSE 追尾与 retry 的完成判据）。

run_id 口径：声明式运行 = task_id（客户端可预置实现边跑边看）；工厂运行 =
factory_runs.id —— 两条链路共用同一步骤表与同一套查询/推流机制。

checkpoint 语义边界（断点续跑）：
- 已 ok 的 tool_call 步永不重放（副作用幂等保护），直接复用 output_json；
- 失败（error）的 llm_call 步在 retry 时原位重跑（row 复用，状态覆盖）；
- 每步边界 db.commit()：进程崩溃后已提交步骤仍可用于续跑。
"""

from sqlalchemy import JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.base import Base, TimestampMixin

STEP_LLM = "llm_call"
STEP_TOOL = "tool_call"
STEP_FINAL = "final"
STEP_TYPES = (STEP_LLM, STEP_TOOL, STEP_FINAL)

STEP_OK = "ok"
STEP_ERROR = "error"
STEP_SKIPPED = "skipped"
STEP_STATUSES = (STEP_OK, STEP_ERROR, STEP_SKIPPED)


class AgentRunStep(Base, TimestampMixin):
    __tablename__ = "agent_run_steps"

    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=STEP_OK)
    tool_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    input_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)


def list_steps(db: Session, *, tenant_id: str, run_id: str) -> list[AgentRunStep]:
    """租户边界内的全部步骤（step_index 升序）；跨租户天然查不到（→ 调用方 404）。"""
    return list(
        db.query(AgentRunStep)
        .filter(AgentRunStep.tenant_id == tenant_id, AgentRunStep.run_id == run_id)
        .order_by(AgentRunStep.step_index.asc(), AgentRunStep.id.asc())
        .all()
    )


def is_run_completed(steps: list[AgentRunStep]) -> bool:
    """完成判据：存在 final 步（成功/truncated 成功都会写 final ok）。"""
    return any(s.step_type == STEP_FINAL and s.status == STEP_OK for s in steps)


def record_step(
    db: Session,
    *,
    tenant_id: str,
    run_id: str,
    step_type: str,
    status: str = STEP_OK,
    step_index: int | None = None,
    tool_code: str | None = None,
    input_json: dict | None = None,
    output_json: dict | None = None,
    duration_ms: int = 0,
    error_text: str | None = None,
    commit: bool = True,
) -> AgentRunStep:
    """落一行步骤 checkpoint；默认立即 commit（checkpoint 必须比答案更 durable）。"""
    if step_index is None:
        step_index = next_step_index(db, run_id=run_id)
    row = AgentRunStep(
        tenant_id=tenant_id,
        run_id=run_id,
        step_index=step_index,
        step_type=step_type,
        status=status,
        tool_code=tool_code,
        input_json=input_json,
        output_json=output_json,
        duration_ms=max(0, int(duration_ms)),
        error_text=error_text[:2000] if error_text else None,
    )
    db.add(row)
    if commit:
        db.commit()
    else:
        db.flush()
    return row


def next_step_index(db: Session, *, run_id: str) -> int:
    """跨租户取号即可：step_index 仅是 run 内序号，租户归属由行上的 tenant_id 决定。"""
    from sqlalchemy import func

    current = (
        db.query(func.max(AgentRunStep.step_index)).filter(AgentRunStep.run_id == run_id).scalar()
    )
    return int(current) + 1 if current is not None else 0


def step_out(row: AgentRunStep) -> dict:
    """API 序列化（steps 列表 / SSE 帧共用）。"""
    return {
        "id": row.id,
        "run_id": row.run_id,
        "step_index": row.step_index,
        "step_type": row.step_type,
        "status": row.status,
        "tool_code": row.tool_code,
        "duration_ms": row.duration_ms,
        "error_text": row.error_text,
        "input_json": row.input_json,
        "output_json": row.output_json,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
