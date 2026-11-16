"""政策种子单测：条数、可溯源、幂等。"""

from app.modules.policy.seed import DEFAULT_POLICIES, seed_default_policies


def test_seed_default_policies_is_traceable_and_idempotent(db_session):
    added = seed_default_policies(db_session, "yard-seed")
    assert added == len(DEFAULT_POLICIES) >= 6

    rows = db_session.query_all = None  # noqa: F841 —— 避免误用未定义属性
    from app.modules.policy.models import Policy

    policies = db_session.query(Policy).filter(Policy.yard_id == "yard-seed").all()
    assert len(policies) == added
    for row in policies:
        assert row.source_url and row.source_url.startswith("http")
        assert row.review_status == "verified"
        assert row.content and "待补充" not in row.content
        assert row.source_hash and len(row.source_hash) == 64

    assert seed_default_policies(db_session, "yard-seed") == 0
