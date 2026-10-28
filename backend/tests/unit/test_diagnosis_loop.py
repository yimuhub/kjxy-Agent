"""作物诊断闭环（v3.9）：留档、用药护栏、专家复核、跟进与安全间隔。"""

from datetime import date

import pytest
from app.core.errors import AppError
from app.core.security import create_access_token
from app.modules.auth import repository as auth_repo
from app.modules.farm import diagnosis_service
from app.modules.farm.schemas import (
    DiagnosisCreate,
    DiagnosisReviewRequest,
    FollowUpCreate,
    PesticideCreate,
)
from app.modules.yards import repository as yard_repo


@pytest.fixture()
def world(db_session):
    yard = yard_repo.create_yard(db_session, name="诊断小院")
    other = yard_repo.create_yard(db_session, name="别家小院")
    expert = auth_repo.create_user(db_session, username="diag-expert", password="pw-diag")
    operator = auth_repo.create_user(db_session, username="diag-operator", password="pw-diag")
    viewer = auth_repo.create_user(db_session, username="diag-viewer", password="pw-diag")
    yard_repo.add_membership(db_session, yard_id=yard.id, user_id=expert.id, role="expert")
    yard_repo.add_membership(db_session, yard_id=yard.id, user_id=operator.id, role="operator")
    yard_repo.add_membership(db_session, yard_id=yard.id, user_id=viewer.id, role="viewer")
    db_session.commit()
    return {"yard": yard, "other": other, "expert": expert, "operator": operator, "viewer": viewer}


def _diagnose(db, world, symptoms="叶片发黄，长势弱"):
    return diagnosis_service.create_diagnosis(
        db,
        world["yard"].id,
        world["operator"].id,
        DiagnosisCreate(crop="水稻", symptoms=symptoms, growth_stage="分蘖期"),
    )


def test_diagnosis_persisted_with_candidates(db_session, world):
    data = _diagnose(db_session, world)
    # 无知识库证据时置信度低（0.25），按规则进入专家复核
    assert data["status"] == "expert_review"
    assert data["needs_expert_review"] is True
    assert data["vision_status"] == "not_configured"
    assert data["output"]["candidates"]
    assert data["medication_guard"] is None

    items, total = diagnosis_service.list_diagnoses(db_session, world["yard"].id)
    assert total == 1
    assert items[0]["top_candidate"] == data["output"]["candidates"][0]["name"]


def test_medication_intent_flags_review_and_lists_registered_products(db_session, world):
    diagnosis_service.create_pesticide(
        db_session,
        world["yard"].id,
        world["expert"].id,
        PesticideCreate(
            name="示例杀菌剂",
            active_ingredient="示例成分",
            registered_crops=["水稻"],
            pre_harvest_interval_days=21,
        ),
    )
    data = _diagnose(db_session, world, "叶片有斑点，请问用什么药？")
    assert data["needs_expert_review"] is True
    assert data["status"] == "expert_review"
    guard = data["medication_guard"]
    assert guard["requires_expert_confirmation"] is True
    assert guard["registered_products"][0]["pre_harvest_interval_days"] == 21
    assert "不构成推荐" in guard["note"]


def test_expert_review_and_follow_up(db_session, world):
    data = _diagnose(db_session, world)
    reviewed = diagnosis_service.review_diagnosis(
        db_session,
        world["yard"].id,
        data["diagnosis_id"],
        world["expert"].id,
        DiagnosisReviewRequest(
            expert_note="现场确认为缺氮，建议追肥并观察新叶。",
            final_diagnosis="缺氮",
            status="confirmed",
        ),
    )
    assert reviewed["status"] == "confirmed"
    assert reviewed["expert_reviewer_id"] == world["expert"].id
    assert reviewed["final_diagnosis"] == "缺氮"

    follow = diagnosis_service.add_follow_up(
        db_session,
        world["yard"].id,
        data["diagnosis_id"],
        world["operator"].id,
        FollowUpCreate(occurred_on=date(2026, 6, 2), note="追肥后新叶转绿。"),
    )
    assert follow["note"] == "追肥后新叶转绿。"
    detail = diagnosis_service.get_diagnosis(db_session, world["yard"].id, data["diagnosis_id"])
    assert len(detail["follow_ups"]) == 1


def test_cross_yard_diagnosis_is_404(db_session, world):
    data = _diagnose(db_session, world)
    with pytest.raises(AppError) as exc:
        diagnosis_service.get_diagnosis(db_session, world["other"].id, data["diagnosis_id"])
    assert exc.value.code == "DIAGNOSIS_NOT_FOUND"


def test_safe_harvest_adds_interval(db_session, world):
    product = diagnosis_service.create_pesticide(
        db_session,
        world["yard"].id,
        world["operator"].id,
        PesticideCreate(
            name="示例杀虫剂",
            registered_crops=["水稻"],
            pre_harvest_interval_days=14,
        ),
    )
    result = diagnosis_service.safe_harvest(
        db_session, world["yard"].id, product["pesticide_id"], date(2026, 6, 1)
    )
    assert result["earliest_harvest_on"] == date(2026, 6, 15)


# ---------------------------------------------------------------------------
# HTTP 权限：viewer 只看、operator 可建、expert 可复核
# ---------------------------------------------------------------------------


def _hdr(user, yard, roles) -> dict[str, str]:
    token = create_access_token(user_id=user.id, yard_id=yard.id, roles=roles)
    return {"Authorization": f"Bearer {token}"}


def test_api_diagnosis_permissions(client, world):
    hdr_operator = _hdr(world["operator"], world["yard"], ["operator"])
    hdr_viewer = _hdr(world["viewer"], world["yard"], ["viewer"])
    hdr_expert = _hdr(world["expert"], world["yard"], ["expert"])

    created = client.post(
        "/api/v1/farm/diagnoses",
        json={"crop": "水稻", "symptoms": "叶片发黄，长势弱", "growth_stage": "分蘖期"},
        headers=hdr_operator,
    )
    assert created.status_code == 201, created.text
    diagnosis_id = created.json()["data"]["diagnosis_id"]

    assert client.get("/api/v1/farm/diagnoses", headers=hdr_viewer).status_code == 200
    denied = client.post(
        "/api/v1/farm/diagnoses",
        json={"crop": "水稻", "symptoms": "发黄"},
        headers=hdr_viewer,
    )
    assert denied.status_code == 403

    reviewed = client.post(
        f"/api/v1/farm/diagnoses/{diagnosis_id}/review",
        json={"expert_note": "确认缺氮", "final_diagnosis": "缺氮"},
        headers=hdr_expert,
    )
    assert reviewed.status_code == 200, reviewed.text

    forbidden_review = client.post(
        f"/api/v1/farm/diagnoses/{diagnosis_id}/review",
        json={"expert_note": "越权"},
        headers=hdr_operator,
    )
    assert forbidden_review.status_code == 403
