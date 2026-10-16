"""任务状态机。状态转移只能经由 TaskService.set_status，不得直接改库。

状态图（开发文档 §7.5）：

    queued → running → succeeded
                       ├→ failed
                       ├→ cancelled
                       └→ awaiting_review → succeeded / failed / cancelled

改变本状态机需要 ADR（开发文档 §2.5）。
"""

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_AWAITING_REVIEW = "awaiting_review"

ALL_STATUSES = frozenset(
    {
        STATUS_QUEUED,
        STATUS_RUNNING,
        STATUS_SUCCEEDED,
        STATUS_FAILED,
        STATUS_CANCELLED,
        STATUS_AWAITING_REVIEW,
    }
)

#: 终态：进入后不允许任何转移
TERMINAL_STATUSES = frozenset({STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLED})

TRANSITIONS: dict[str, list[str]] = {
    STATUS_QUEUED: [STATUS_RUNNING, STATUS_CANCELLED],
    STATUS_RUNNING: [STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLED, STATUS_AWAITING_REVIEW],
    STATUS_AWAITING_REVIEW: [STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLED],
    STATUS_SUCCEEDED: [],
    STATUS_FAILED: [],
    STATUS_CANCELLED: [],
}


def can_transition(from_status: str, to_status: str) -> bool:
    """是否允许从 from_status 转移到 to_status。"""
    return to_status in TRANSITIONS.get(from_status, [])
