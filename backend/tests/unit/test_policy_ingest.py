"""政策采集单元测试：去重 409 / source_hash 生成 / yard 隔离 / 过期标记。"""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.policy import service
from app.modules.policy.schemas import PolicyIngestRequest

YARD_A = "yard-a"
YARD_B = "yard-b"


def _ctx(yard_id: str) -> YardContext:
    return YardContext(yard_id=yard_id, user_id="user-1", roles=["operator"], request_id="req-test")


def _req(**kw) -> PolicyIngestRequest:
    base = dict(
        policy_name="水稻规模化种植补贴",
        publish_department="农业农村局",
        scope="province",
        province="湖南",
        policy_type="种植补贴",
        apply_conditions_json={
            "crops": ["水稻"],
            "min_area_mu": 50,
            "applicant_types": ["合作社"],
        },
        material_requirements_json={"materials": ["营业执照", "土地流转合同"]},
        content="补贴原文内容……",
        source_url="https://example.gov.cn/policy/1",
    )
    base.update(kw)
    return PolicyIngestRequest(**base)


def test_ingest_ok_and_hash(db_session):
    policy = service.ingest_policy(db_session, _ctx(YARD_A), _req())
    assert policy.id
    assert policy.yard_id == YARD_A
    assert policy.status == "active"
    assert policy.review_status == "pending"
    assert policy.first_crawled_at is not None
    expected = hashlib.sha256(
        ("补贴原文内容……" + "\n" + "https://example.gov.cn/policy/1").encode("utf-8")
    ).hexdigest()
    assert policy.source_hash == expected


def test_ingest_duplicate_conflict_409(db_session):
    service.ingest_policy(db_session, _ctx(YARD_A), _req())
    with pytest.raises(AppError) as exc_info:
        service.ingest_policy(db_session, _ctx(YARD_A), _req())
    assert exc_info.value.code == "POLICY_DUPLICATE"
    assert exc_info.value.http_status == 409


def test_ingest_same_name_different_yard_ok(db_session):
    """同名同发布机构在不同小院下互不干扰。"""
    service.ingest_policy(db_session, _ctx(YARD_A), _req())
    policy_b = service.ingest_policy(db_session, _ctx(YARD_B), _req())
    assert policy_b.yard_id == YARD_B

    items_a, total_a = service.list_policies(db_session, _ctx(YARD_A), 1, 20)
    items_b, total_b = service.list_policies(db_session, _ctx(YARD_B), 1, 20)
    assert total_a == 1 and total_b == 1
    assert items_a[0].yard_id == YARD_A
    assert items_b[0].yard_id == YARD_B


def test_get_policy_yard_isolation(db_session):
    policy = service.ingest_policy(db_session, _ctx(YARD_A), _req())
    with pytest.raises(AppError) as exc_info:
        service.get_policy(db_session, _ctx(YARD_B), policy.id)
    assert exc_info.value.code == "POLICY_NOT_FOUND"
    assert exc_info.value.http_status == 404


def test_mark_expired_on_list(db_session):
    past = datetime.now(UTC) - timedelta(days=30)
    policy = service.ingest_policy(
        db_session,
        _ctx(YARD_A),
        _req(policy_name="已过期补贴", apply_end_at=past),
    )
    assert policy.status == "active"

    marked = service.mark_expired(db_session, YARD_A)
    assert marked == 1

    items, _ = service.list_policies(db_session, _ctx(YARD_A), 1, 20, status="expired")
    assert any(i.id == policy.id for i in items)
    items_active, _ = service.list_policies(db_session, _ctx(YARD_A), 1, 20, status="active")
    assert all(i.id != policy.id for i in items_active)


def test_list_auto_marks_expired(db_session):
    """GET list 时对过期未标记的自动更新，无需管理接口。"""
    past = datetime.now(UTC) - timedelta(days=1)
    policy = service.ingest_policy(
        db_session,
        _ctx(YARD_A),
        _req(policy_name="刚过期的补贴", apply_end_at=past),
    )
    items, _ = service.list_policies(db_session, _ctx(YARD_A), 1, 20)
    by_id = {i.id: i for i in items}
    assert by_id[policy.id].status == "expired"
