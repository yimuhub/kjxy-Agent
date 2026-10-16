"""Skill 灰度发布单元测试：名单命中/未命中、百分比边界、promote/halt、租户隔离。"""

import pytest
from app.core.deps import TenantContext
from app.core.errors import AppError
from app.modules.evolution import rollout, service


def _ctx(tenant="t1", roles=("expert",)) -> TenantContext:
    return TenantContext(tenant_id=tenant, user_id="u1", roles=list(roles), request_id="req_test")


def _publish(db, ctx, code="gray_skill", version="1.0.0"):
    s = service.create_skill(
        db, ctx, skill_code=code, version=version, definition={"name": code, "v": version}
    )
    service.review_skill(db, ctx, s.id, "submit", None)
    service.review_skill(db, ctx, s.id, "submit", None)
    return service.review_skill(db, ctx, s.id, "approve", None)


def _setup_two_versions(db, code="gray_skill"):
    ctx = _ctx()
    v1 = _publish(db, ctx, code=code, version="1.0.0")
    v2 = _publish(db, ctx, code=code, version="2.0.0")
    return ctx, v1, v2


# ---------------------------------------------------------------- allowlist
def test_allowlist_hit_and_miss(db_session):
    ctx, v1, v2 = _setup_two_versions(db_session)
    rollout.set_rollout(
        db_session,
        ctx,
        v1.id,
        rollout_version="2.0.0",
        strategy="tenant_allowlist",
        tenant_ids=["t1", "t9"],
    )
    assert rollout.resolve_skill_version(db_session, "t1", "gray_skill") == "2.0.0"
    assert rollout.resolve_skill_version(db_session, "t2", "gray_skill") == "1.0.0"


def test_allowlist_empty_tenant_ids_rejected(db_session):
    ctx, v1, v2 = _setup_two_versions(db_session)
    with pytest.raises(AppError) as e:
        rollout.set_rollout(
            db_session,
            ctx,
            v1.id,
            rollout_version="2.0.0",
            strategy="tenant_allowlist",
            tenant_ids=[],
        )
    assert e.value.code == "INVALID_ROLLOUT_STRATEGY"


def test_invalid_strategy_rejected(db_session):
    ctx, v1, v2 = _setup_two_versions(db_session)
    with pytest.raises(AppError) as e:
        rollout.set_rollout(
            db_session, ctx, v1.id, rollout_version="2.0.0", strategy="random_sample"
        )
    assert e.value.code == "INVALID_ROLLOUT_STRATEGY"


def test_rollout_version_must_be_published(db_session):
    ctx, v1, _v2 = _setup_two_versions(db_session)
    service.create_skill(
        db_session, ctx, skill_code="gray_skill", version="3.0.0", definition={}
    )  # draft，未发布
    with pytest.raises(AppError) as e:
        rollout.set_rollout(
            db_session,
            ctx,
            v1.id,
            rollout_version="3.0.0",
            strategy="percentage",
            percentage=100,
        )
    assert e.value.code == "SKILL_NOT_PUBLISHED"


def test_rollout_version_must_differ_from_stable(db_session):
    ctx, v1, v2 = _setup_two_versions(db_session)
    with pytest.raises(AppError) as e:
        rollout.set_rollout(
            db_session,
            ctx,
            v1.id,
            rollout_version="1.0.0",
            strategy="percentage",
            percentage=100,
        )
    assert e.value.code == "INVALID_ROLLOUT_VERSION"


def test_rollout_requires_review_permission(db_session):
    ctx, v1, v2 = _setup_two_versions(db_session)
    member = _ctx(roles=("member",))
    with pytest.raises(AppError) as e:
        rollout.set_rollout(
            db_session,
            member,
            v1.id,
            rollout_version="2.0.0",
            strategy="percentage",
            percentage=50,
        )
    assert e.value.code == "FORBIDDEN"


# ---------------------------------------------------------------- percentage
def test_percentage_zero_never_hits(db_session):
    ctx, v1, v2 = _setup_two_versions(db_session)
    rollout.set_rollout(
        db_session,
        ctx,
        v1.id,
        rollout_version="2.0.0",
        strategy="percentage",
        percentage=0,
    )
    for tid in ("t1", "t2", "t3", "some-tenant", "another-one"):
        assert rollout.resolve_skill_version(db_session, tid, "gray_skill") == "1.0.0"


def test_percentage_hundred_always_hits(db_session):
    ctx, v1, v2 = _setup_two_versions(db_session)
    rollout.set_rollout(
        db_session,
        ctx,
        v1.id,
        rollout_version="2.0.0",
        strategy="percentage",
        percentage=100,
    )
    for tid in ("t1", "t2", "t3", "some-tenant", "another-one"):
        assert rollout.resolve_skill_version(db_session, tid, "gray_skill") == "2.0.0"


def test_percentage_partial_is_stable_and_deterministic(db_session):
    ctx, v1, v2 = _setup_two_versions(db_session)
    rollout.set_rollout(
        db_session,
        ctx,
        v1.id,
        rollout_version="2.0.0",
        strategy="percentage",
        percentage=50,
    )
    results = {
        tid: rollout.resolve_skill_version(db_session, tid, "gray_skill")
        for tid in (f"tenant-{i}" for i in range(40))
    }
    assert set(results.values()) <= {"1.0.0", "2.0.0"}
    # 同一租户多次解析结果一致
    for tid, ver in results.items():
        assert rollout.resolve_skill_version(db_session, tid, "gray_skill") == ver


def test_percentage_out_of_range_rejected(db_session):
    ctx, v1, v2 = _setup_two_versions(db_session)
    for bad in (-1, 101):
        with pytest.raises(AppError) as e:
            rollout.set_rollout(
                db_session,
                ctx,
                v1.id,
                rollout_version="2.0.0",
                strategy="percentage",
                percentage=bad,
            )
        assert e.value.code == "INVALID_ROLLOUT_STRATEGY"


# ---------------------------------------------------------------- 无灰度 / promote / halt
def test_no_rollout_returns_published_version(db_session):
    ctx = _ctx()
    _publish(db_session, ctx, version="1.0.0")
    assert rollout.resolve_skill_version(db_session, "t1", "gray_skill") == "1.0.0"


def test_no_rollout_no_published_returns_none(db_session):
    assert rollout.resolve_skill_version(db_session, "t1", "no_such_skill") is None


def test_promote_makes_rollout_full(db_session):
    ctx, v1, v2 = _setup_two_versions(db_session)
    rollout.set_rollout(
        db_session,
        ctx,
        v1.id,
        rollout_version="2.0.0",
        strategy="percentage",
        percentage=30,
    )
    r = rollout.promote_rollout(db_session, ctx, v1.id)
    assert r.status == "completed"
    db_session.refresh(v1)
    assert v1.status == "deprecated"
    # 灰度完成后，解析回到无灰度逻辑：v2 为最新 published
    assert rollout.resolve_skill_version(db_session, "t1", "gray_skill") == "2.0.0"
    # 灰度完成后可发起新一轮灰度
    _publish(db_session, ctx, version="3.0.0")
    r2 = rollout.set_rollout(
        db_session,
        ctx,
        v2.id,
        rollout_version="3.0.0",
        strategy="tenant_allowlist",
        tenant_ids=["t1"],
    )
    assert r2.id != r.id
    assert r2.status == "active"


def test_promote_without_rollout_fails(db_session):
    ctx, v1, v2 = _setup_two_versions(db_session)
    with pytest.raises(AppError) as e:
        rollout.promote_rollout(db_session, ctx, v1.id)
    assert e.value.code == "ROLLOUT_NOT_FOUND"


def test_halt_rolls_back_to_stable(db_session):
    ctx, v1, v2 = _setup_two_versions(db_session)
    rollout.set_rollout(
        db_session,
        ctx,
        v1.id,
        rollout_version="2.0.0",
        strategy="percentage",
        percentage=100,
    )
    assert rollout.resolve_skill_version(db_session, "t1", "gray_skill") == "2.0.0"
    r = rollout.halt_rollout(db_session, ctx, v1.id)
    assert r.status == "paused"
    # 暂停后全部回到稳定版
    assert rollout.resolve_skill_version(db_session, "t1", "gray_skill") == "1.0.0"
    # 重复暂停应失败
    with pytest.raises(AppError) as e:
        rollout.halt_rollout(db_session, ctx, v1.id)
    assert e.value.code == "INVALID_ROLLOUT_STATUS"


def test_halt_without_rollout_fails(db_session):
    ctx, v1, v2 = _setup_two_versions(db_session)
    with pytest.raises(AppError) as e:
        rollout.halt_rollout(db_session, ctx, v1.id)
    assert e.value.code == "ROLLOUT_NOT_FOUND"


def test_get_rollout_none_when_absent(db_session):
    ctx, v1, v2 = _setup_two_versions(db_session)
    assert rollout.get_rollout(db_session, ctx, v1.id) is None


# ---------------------------------------------------------------- 租户隔离
def test_rollout_write_isolation(db_session):
    """t2 不能在 t1 的 Skill 上设置灰度（写操作按配置者租户隔离）。"""
    ctx1 = _ctx(tenant="t1")
    ctx2 = _ctx(tenant="t2")
    v1 = _publish(db_session, ctx1, version="1.0.0")
    _publish(db_session, ctx1, version="2.0.0")
    with pytest.raises(AppError) as e:
        rollout.set_rollout(
            db_session,
            ctx2,
            v1.id,
            rollout_version="2.0.0",
            strategy="percentage",
            percentage=100,
        )
    assert e.value.code == "SKILL_NOT_FOUND"
    # t2 也查不到 t1 的灰度配置（Skill 行本身就不可见 → 404）
    with pytest.raises(AppError) as e:
        rollout.get_rollout(db_session, ctx2, v1.id)
    assert e.value.code == "SKILL_NOT_FOUND"


def test_rollout_owner_preference(db_session):
    """同一 skill_code 多租户各配灰度时，解析优先用请求租户自己的。"""
    ctx1 = _ctx(tenant="t1")
    ctx2 = _ctx(tenant="t2")
    v1_t1 = _publish(db_session, ctx1, version="1.0.0")
    _publish(db_session, ctx1, version="2.0.0")
    rollout.set_rollout(
        db_session,
        ctx1,
        v1_t1.id,
        rollout_version="2.0.0",
        strategy="percentage",
        percentage=100,
    )
    v1_t2 = _publish(db_session, ctx2, version="1.0.0")
    _publish(db_session, ctx2, version="2.0.0")
    rollout.set_rollout(
        db_session,
        ctx2,
        v1_t2.id,
        rollout_version="2.0.0",
        strategy="percentage",
        percentage=0,
    )
    assert rollout.resolve_skill_version(db_session, "t1", "gray_skill") == "2.0.0"
    assert rollout.resolve_skill_version(db_session, "t2", "gray_skill") == "1.0.0"


def test_no_rollout_fallback_is_tenant_scoped(db_session):
    """无灰度时回退只看请求租户自己的 published 版本。"""
    ctx1 = _ctx(tenant="t1")
    _publish(db_session, ctx1, version="1.0.0")
    assert rollout.resolve_skill_version(db_session, "t1", "gray_skill") == "1.0.0"
    assert rollout.resolve_skill_version(db_session, "t2", "gray_skill") is None


def test_set_rollout_upserts_existing(db_session):
    ctx, v1, v2 = _setup_two_versions(db_session)
    r1 = rollout.set_rollout(
        db_session,
        ctx,
        v1.id,
        rollout_version="2.0.0",
        strategy="percentage",
        percentage=20,
    )
    r2 = rollout.set_rollout(
        db_session,
        ctx,
        v1.id,
        rollout_version="2.0.0",
        strategy="tenant_allowlist",
        tenant_ids=["t1"],
    )
    assert r2.id == r1.id  # 同 code 只保留一条未完成灰度
    assert r2.strategy == "tenant_allowlist"
    assert rollout.resolve_skill_version(db_session, "t1", "gray_skill") == "2.0.0"
