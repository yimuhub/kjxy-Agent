"""政策规则匹配单元测试：四档各至少 1 例、insufficient 不误标、条件清单正确。

首版只做规则比对，不做成功率预测。
"""

from app.core.deps import YardContext
from app.modules.policy import service
from app.modules.policy.schemas import PolicyIngestRequest, PolicyProfile

YARD_A = "yard-a"
YARD_B = "yard-b"


def _ctx(yard_id: str) -> YardContext:
    return YardContext(yard_id=yard_id, user_id="user-1", roles=["operator"], request_id="req-test")


def _seed(db_session):
    """四条政策，分别对应 eligible / ineligible / insufficient / partial 的期望。"""
    ctx = _ctx(YARD_A)
    # P1：省政策，作物+规模+主体+省份全声明
    service.ingest_policy(
        db_session,
        ctx,
        PolicyIngestRequest(
            policy_name="水稻规模化种植补贴",
            publish_department="农业农村局",
            scope="province",
            province="湖南",
            apply_conditions_json={
                "crops": ["水稻"],
                "min_area_mu": 50,
                "applicant_types": ["合作社"],
            },
            source_url="https://example.gov.cn/policy/1",
        ),
    )
    # P2：只看作物，画像不符
    service.ingest_policy(
        db_session,
        ctx,
        PolicyIngestRequest(
            policy_name="小麦良种补贴",
            publish_department="粮食局",
            scope="national",
            apply_conditions_json={"crops": ["小麦"]},
        ),
    )
    # P3：要求主体类型，画像缺该字段
    service.ingest_policy(
        db_session,
        ctx,
        PolicyIngestRequest(
            policy_name="全国农机购置补贴",
            publish_department="农业农村部",
            scope="national",
            apply_conditions_json={
                "min_area_mu": 200,
                "applicant_types": ["企业"],
            },
        ),
    )
    # P4：未声明任何规则条件
    service.ingest_policy(
        db_session,
        ctx,
        PolicyIngestRequest(
            policy_name="农业技术培训通知",
            publish_department="农技站",
            scope="national",
        ),
    )


def _full_profile() -> PolicyProfile:
    return PolicyProfile(
        crops=["水稻", "玉米"],
        area_mu=100,
        province="湖南",
        applicant_type="合作社",
    )


def _by_name(results):
    return {r.policy_name: r for r in results}


def test_eligible_all_conditions_hit(db_session):
    _seed(db_session)
    by_name = _by_name(service.match_policies(db_session, _ctx(YARD_A), _full_profile()))
    r = by_name["水稻规模化种植补贴"]
    assert r.match_level == "eligible"
    assert r.matched_conditions == ["crops", "min_area_mu", "applicant_types", "province"]
    assert r.unmet_conditions == []
    assert r.unknown_conditions == []
    assert r.source_url == "https://example.gov.cn/policy/1"
    assert r.verification_status == "pending"


def test_ineligible_on_crop_mismatch(db_session):
    _seed(db_session)
    by_name = _by_name(service.match_policies(db_session, _ctx(YARD_A), _full_profile()))
    r = by_name["小麦良种补贴"]
    assert r.match_level == "ineligible"
    assert r.unmet_conditions == ["crops"]
    assert r.matched_conditions == []
    assert r.unknown_conditions == []


def test_insufficient_when_profile_missing_key_fields(db_session):
    _seed(db_session)
    profile = PolicyProfile(crops=["水稻"], area_mu=300, province="湖南")
    by_name = _by_name(service.match_policies(db_session, _ctx(YARD_A), profile))
    r = by_name["全国农机购置补贴"]
    # 画像缺 applicant_type：不得误标 eligible，也不得误标为不命中
    assert r.match_level == "insufficient"
    assert r.unknown_conditions == ["applicant_types"]
    assert r.matched_conditions == ["min_area_mu"]
    assert r.unmet_conditions == []


def test_insufficient_not_mismarked_for_geo_missing(db_session):
    """画像缺 province 时，省政策画像也应判 insufficient 而非 eligible。"""
    _seed(db_session)
    profile = PolicyProfile(crops=["水稻"], area_mu=100, applicant_type="合作社")
    by_name = _by_name(service.match_policies(db_session, _ctx(YARD_A), profile))
    r = by_name["水稻规模化种植补贴"]
    assert r.match_level != "eligible"
    assert r.match_level == "insufficient"
    assert "province" in r.unknown_conditions


def test_partial_when_no_conditions_declared(db_session):
    _seed(db_session)
    by_name = _by_name(service.match_policies(db_session, _ctx(YARD_A), _full_profile()))
    r = by_name["农业技术培训通知"]
    assert r.match_level == "partial"
    assert r.matched_conditions == []
    assert r.unmet_conditions == []
    assert r.unknown_conditions == []


def test_match_yard_isolation(db_session):
    _seed(db_session)
    service.ingest_policy(
        db_session,
        _ctx(YARD_B),
        PolicyIngestRequest(
            policy_name="他小院的政策",
            publish_department="农业农村局",
            scope="national",
        ),
    )
    results = service.match_policies(db_session, _ctx(YARD_A), _full_profile())
    assert "他小院的政策" not in {r.policy_name for r in results}


def test_match_sorted_by_level(db_session):
    _seed(db_session)
    results = service.match_policies(db_session, _ctx(YARD_A), _full_profile())
    rank = {"eligible": 0, "partial": 1, "insufficient": 2, "ineligible": 3}
    levels = [rank[r.match_level] for r in results]
    assert levels == sorted(levels)
