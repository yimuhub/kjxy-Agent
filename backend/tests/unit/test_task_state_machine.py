"""任务状态机单元测试：全部合法转移与非法转移。"""

import pytest
from app.modules.tasks import state_machine as sm


def test_status_constants():
    assert sm.STATUS_QUEUED == "queued"
    assert sm.STATUS_RUNNING == "running"
    assert sm.STATUS_SUCCEEDED == "succeeded"
    assert sm.STATUS_FAILED == "failed"
    assert sm.STATUS_CANCELLED == "cancelled"
    assert sm.STATUS_AWAITING_REVIEW == "awaiting_review"
    assert sm.ALL_STATUSES == {
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "awaiting_review",
    }


def test_transitions_match_spec():
    assert sm.TRANSITIONS == {
        "queued": ["running", "cancelled"],
        "running": ["succeeded", "failed", "cancelled", "awaiting_review"],
        "awaiting_review": ["succeeded", "failed", "cancelled"],
        "succeeded": [],
        "failed": [],
        "cancelled": [],
    }


def test_all_legal_transitions():
    for from_status, to_list in sm.TRANSITIONS.items():
        for to_status in to_list:
            assert sm.can_transition(from_status, to_status), f"{from_status} -> {to_status}"


@pytest.mark.parametrize(
    "from_status,to_status",
    [
        # queued 非法目标
        ("queued", "queued"),
        ("queued", "succeeded"),
        ("queued", "failed"),
        ("queued", "awaiting_review"),
        # running 非法目标
        ("running", "queued"),
        ("running", "running"),
        # awaiting_review 非法目标
        ("awaiting_review", "queued"),
        ("awaiting_review", "running"),
        ("awaiting_review", "awaiting_review"),
        # 终态：任何转出都非法
        ("succeeded", "queued"),
        ("succeeded", "running"),
        ("succeeded", "failed"),
        ("succeeded", "cancelled"),
        ("succeeded", "awaiting_review"),
        ("failed", "queued"),
        ("failed", "running"),
        ("failed", "succeeded"),
        ("failed", "cancelled"),
        ("cancelled", "queued"),
        ("cancelled", "running"),
        ("cancelled", "succeeded"),
        ("cancelled", "failed"),
        # 未知状态
        ("unknown", "queued"),
        ("queued", "unknown"),
        ("", "queued"),
    ],
)
def test_illegal_transitions(from_status, to_status):
    assert not sm.can_transition(from_status, to_status)


def test_terminal_statuses_have_no_outgoing():
    for status in sm.TERMINAL_STATUSES:
        assert sm.TRANSITIONS[status] == []
        for other in sm.ALL_STATUSES:
            assert not sm.can_transition(status, other)
