"""指挥官 token 账本（v3.71 从 service.py 拆出）。

只放「按 task_id 前缀族归集 LLM 用量」的纯查询函数。service.py 体量已超
2700 行，先把边界最清晰的账本读数拆出来；执行/调度逻辑仍在 service。
"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.modules.llm.models import LLMCall

log = get_logger(__name__)


def subtask_ledger_tokens(db: Session, run_id: str, seq: int) -> tuple[int, int] | None:
    """``llm_calls`` 账本里第 ``seq`` 号子任务已花掉的 (input, output) token。

    只认带 seq 的 task_id 前缀族——``cmd-<run>-<seq>-`` 是助手执行、
    ``cmd-self-<run>-<seq>-`` 是指挥官亲自执行——所以并行的兄弟子任务不会串账。
    文档链路（``chat-doc-*``）不带 seq，仍由 ``_invoke_agent`` 的自报值兜住。
    读不到时返回 ``None``：上一次调用失败可能把 Session 留在待回滚状态，记账不能拖垮执行。
    """
    try:
        row = db.execute(
            select(
                func.coalesce(func.sum(LLMCall.input_tokens), 0),
                func.coalesce(func.sum(LLMCall.output_tokens), 0),
            ).where(
                or_(
                    LLMCall.task_id.like(f"cmd-{run_id[:12]}-{seq}-%"),
                    LLMCall.task_id.like(f"cmd-self-{run_id[:10]}-{seq}-%"),
                )
            )
        ).one()
    except Exception as exc:  # noqa: BLE001 - 只为尽力读数，失败即放弃本轮记账
        log.warning("commander ledger read failed run=%s seq=%s: %s", run_id, seq, exc)
        return None
    return int(row[0] or 0), int(row[1] or 0)
