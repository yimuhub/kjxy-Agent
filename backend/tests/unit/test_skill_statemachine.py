"""Skill 状态机单元测试：完整生命周期、非法转移、版本冲突、回滚。"""

import pytest
from app.core.deps import TenantContext
from app.core.errors import AppError
from app.modules.evolution import service


def _ctx(roles=("member",)) -> TenantContext:
    return TenantContext(tenant_id="t1", user_id="u1", roles=list(roles), request_id="req_test")


def _expert_ctx() -> TenantContext:
    return _ctx(roles=("expert",))


def _make(db, code="demo_skill", version="1.0.0", definition=None):
    return service.create_skill(
        db, _ctx(), skill_code=code, version=version, definition=definition or {"name": code}
    )


def test_skill_full_lifecycle(db_session):
    s = _make(db_session)
    assert s.status == "draft"

    s = service.review_skill(db_session, _ctx(), s.id, "submit", None)  # draft→testing
    assert s.status == "testing"

    s = service.review_skill(db_session, _ctx(), s.id, "submit", None)  # testing→pending_review
    assert s.status == "pending_review"

    s = service.review_skill(db_session, _expert_ctx(), s.id, "approve", None)
    assert s.status == "published"
    assert s.approved_by == "u1"

    # 无历史版本时回滚应报 SKILL_NOT_FOUND
    with pytest.raises(AppError) as e:
        service.review_skill(db_session, _expert_ctx(), s.id, "rollback", None)
    assert e.value.code == "SKILL_NOT_FOUND"


def test_skill_rollback_to_previous_version(db_session):
    v1 = _make(db_session, version="1.0.0")
    for action, ctx in (("submit", _ctx()), ("submit", _ctx()), ("approve", _expert_ctx())):
        v1 = service.review_skill(db_session, ctx, v1.id, action, None)
    assert v1.status == "published"

    v2 = _make(db_session, version="2.0.0")
    for action, ctx in (("submit", _ctx()), ("submit", _ctx()), ("approve", _expert_ctx())):
        v2 = service.review_skill(db_session, ctx, v2.id, action, None)
    assert v2.status == "published"

    service.review_skill(db_session, _expert_ctx(), v2.id, "rollback", None)
    db_session.refresh(v2)
    db_session.refresh(v1)
    assert v2.status == "deprecated"
    assert v1.status == "published"  # 上一版本恢复为 published


def test_skill_rollback_without_history_fails(db_session):
    v1 = _make(db_session, version="1.0.0")
    for action, ctx in (("submit", _ctx()), ("submit", _ctx()), ("approve", _expert_ctx())):
        v1 = service.review_skill(db_session, ctx, v1.id, action, None)
    with pytest.raises(AppError) as e:
        service.review_skill(db_session, _expert_ctx(), v1.id, "rollback", None)
    assert e.value.code == "SKILL_NOT_FOUND"


def test_skill_illegal_transition(db_session):
    s = _make(db_session)
    with pytest.raises(AppError) as e:
        # draft→published 非法
        service.review_skill(db_session, _expert_ctx(), s.id, "approve", None)
    assert e.value.code == "INVALID_SKILL_TRANSITION"
    assert e.value.http_status == 409


def test_skill_reject_back_to_draft(db_session):
    s = _make(db_session)
    service.review_skill(db_session, _ctx(), s.id, "submit", None)
    service.review_skill(db_session, _ctx(), s.id, "submit", None)
    s = service.review_skill(db_session, _expert_ctx(), s.id, "reject", "需要补充示例")
    assert s.status == "draft"


def test_skill_version_conflict_on_create(db_session):
    _make(db_session, version="1.0.0")
    with pytest.raises(AppError) as e:
        _make(db_session, version="1.0.0")
    assert e.value.code == "SKILL_VERSION_CONFLICT"
    assert e.value.http_status == 409


def test_skill_approve_requires_reviewer_role(db_session):
    s = _make(db_session)
    service.review_skill(db_session, _ctx(), s.id, "submit", None)
    service.review_skill(db_session, _ctx(), s.id, "submit", None)
    with pytest.raises(AppError) as e:
        service.review_skill(db_session, _ctx(), s.id, "approve", None)  # member 无审核权限
    assert e.value.code == "FORBIDDEN"


def test_skill_list_tenant_isolation(db_session):
    _make(db_session, code="s1")
    other = TenantContext(tenant_id="t2", user_id="u2", roles=["member"], request_id="r")
    items, total = service.list_skills(db_session, other, 1, 20)
    assert total == 0
    assert items == []
    items, total = service.list_skills(db_session, _ctx(), 1, 20)
    assert total == 1
