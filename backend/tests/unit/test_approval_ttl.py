"""工具审批单 TTL 与复用上限（整改项 7：审批不是永久通行证）。

覆盖：
- 批准时按 settings.tool_approval_ttl_hours 计算 expires_at（0=永久）；
- has_approved_approval 过期后不再放行；
- used_count 随放行累加，达到 tool_approval_max_uses 上限后拦截；
- expires_at 为 NULL 的历史行（向后兼容）视为仍有效。

settings 是进程级单例，用例内直接改写、autouse fixture 统一恢复默认。
"""

from datetime import UTC, datetime, timedelta

import pytest
from app.core.config import settings
from app.modules.tools import approvals as approval_svc
from app.modules.tools.models import ToolApproval

_YARD = "t-ttl"
_TOOL = "test.danger"
_PARAMS = {"batch": "B-1"}


@pytest.fixture(autouse=True)
def _restore_settings():
    yield
    settings.tool_approval_ttl_hours = 24
    settings.tool_approval_max_uses = 1


def _approve(db, *, ttl_hours: int, max_uses: int) -> ToolApproval:
    """按指定 TTL/上限创建并批准一条审批单（申请人 u-1 / 审批人 u-2）。"""
    settings.tool_approval_ttl_hours = ttl_hours
    settings.tool_approval_max_uses = max_uses
    pending = approval_svc.get_or_create_pending_approval(
        db,
        yard_id=_YARD,
        tool_code=_TOOL,
        params=_PARAMS,
        requested_by="u-1",
        request_id="req-ttl",
    )
    db.flush()
    approval_svc.approve_approval(db, pending, decided_by="u-2", request_id="req-ttl")
    db.flush()
    return pending


def _grant(db, params: dict | None = None) -> bool:
    return approval_svc.has_approved_approval(
        db, yard_id=_YARD, tool_code=_TOOL, params=params or _PARAMS
    )


def test_approve_sets_expires_at_by_ttl(db_session):
    a = _approve(db_session, ttl_hours=24, max_uses=0)
    assert a.expires_at is not None
    assert abs((a.expires_at - a.decided_at - timedelta(hours=24)).total_seconds()) < 2
    assert a.used_count == 0


def test_approve_with_ttl_zero_means_permanent(db_session):
    a = _approve(db_session, ttl_hours=0, max_uses=0)
    assert a.expires_at is None
    # 无 TTL + 无上限：可多次放行
    for _ in range(3):
        assert _grant(db_session)
    assert a.used_count == 3


def test_expired_approval_no_longer_grants(db_session):
    a = _approve(db_session, ttl_hours=24, max_uses=0)
    a.expires_at = datetime.now(UTC) - timedelta(minutes=1)  # 手动置为已过期
    db_session.flush()
    assert _grant(db_session) is False
    assert a.used_count == 0  # 过期不放行，也不计次


def test_null_expires_at_backward_compat(db_session):
    a = _approve(db_session, ttl_hours=24, max_uses=0)
    a.expires_at = None  # 历史行：未计算有效期
    db_session.flush()
    assert _grant(db_session)
    assert a.used_count == 1


def test_max_uses_caps_reuse(db_session):
    a = _approve(db_session, ttl_hours=24, max_uses=2)
    assert _grant(db_session)
    assert _grant(db_session)
    # 第 3 次达到上限 → 拦截（需重新发起审批）
    assert _grant(db_session) is False
    assert a.used_count == 2


def test_different_params_not_reused(db_session):
    _approve(db_session, ttl_hours=24, max_uses=0)
    assert _grant(db_session, {"batch": "B-2"}) is False
