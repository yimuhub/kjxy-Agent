"""政策申报材料草案单元测试（第三期 Worker B）。

覆盖：草案生成（checklist / missing / notes / 恒 draft）、
缺失项识别、小院隔离（看不到其他小院的政策与草案）。
"""

from app.core.security import create_access_token
from app.modules.policy.models import Policy, PolicyApplication

MATERIALS = {
    "materials": [
        {"name": "身份证复印件", "required": True},
        {"name": "营业执照", "required": True},
        {"name": "土地流转合同", "required": True},
        {"name": "银行流水（可选）", "required": False},
    ]
}


def _headers(yard_id: str = "t1", user_id: str = "u1"):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=["yard_admin"])
    return {"Authorization": f"Bearer {token}"}


def _seed_policy(db_session, yard_id="t1", **overrides):
    data = {
        "yard_id": yard_id,
        "policy_name": "高标准农田建设补贴",
        "publish_department": "农业农村局",
        "status": "active",
        "review_status": "verified",
        "material_requirements_json": MATERIALS,
    }
    data.update(overrides)
    policy = Policy(**data)
    db_session.add(policy)
    db_session.commit()
    db_session.refresh(policy)
    return policy


def _draft_payload(**overrides):
    data = {
        "applicant_name": "张三",
        "applicant_type": "家庭农场",
        "contact_info": {"phone": "13800001111", "address": "某县某镇"},
        "form_data": {"身份证复印件": "已准备"},
    }
    data.update(overrides)
    return data


# ---------- 草案生成 ----------


def test_create_draft_generates_checklist_and_missing(client, db_session):
    policy = _seed_policy(db_session)
    r = client.post(
        f"/api/v1/policies/{policy.id}/application-drafts",
        json=_draft_payload(),
        headers=_headers(),
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["draft_id"]
    assert body["status"] == "draft"
    assert body["verification_reminder"] == "本材料为系统生成的草案，需人工核对后提交"

    checklist = body["material_checklist"]
    assert len(checklist) == 4
    assert checklist[0]["name"] == "身份证复印件"
    assert checklist[0]["required"] is True
    assert all(c["source"] == "政策要求" for c in checklist)

    # 必填且未填的两项 → 缺失；已填的身份证复印件不在缺失里；可选的不算缺失
    assert set(body["missing_materials"]) == {"营业执照", "土地流转合同"}

    notes = body["notes"]
    assert "草案声明" in notes
    assert "不承诺申报成功" in notes


def test_create_draft_requires_applicant_name(client, db_session):
    policy = _seed_policy(db_session)
    r = client.post(
        f"/api/v1/policies/{policy.id}/application-drafts",
        json=_draft_payload(applicant_name="   "),
        headers=_headers(),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_REQUEST"


def test_create_draft_policy_not_found(client, db_session):
    r = client.post(
        "/api/v1/policies/does-not-exist/application-drafts",
        json=_draft_payload(),
        headers=_headers(),
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "POLICY_NOT_FOUND"


def test_create_draft_all_required_filled_no_missing(client, db_session):
    policy = _seed_policy(db_session)
    r = client.post(
        f"/api/v1/policies/{policy.id}/application-drafts",
        json=_draft_payload(
            form_data={
                "身份证复印件": "已准备",
                "营业执照": "已准备",
                "土地流转合同": "已准备",
            }
        ),
        headers=_headers(),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["missing_materials"] == []


def test_blank_form_values_count_as_missing(client, db_session):
    policy = _seed_policy(db_session)
    r = client.post(
        f"/api/v1/policies/{policy.id}/application-drafts",
        json=_draft_payload(form_data={"身份证复印件": "  ", "营业执照": None}),
        headers=_headers(),
    )
    assert r.status_code == 200, r.text
    missing = r.json()["data"]["missing_materials"]
    assert "身份证复印件" in missing
    assert "营业执照" in missing
    assert "土地流转合同" in missing


# ---------- 查看草案 ----------


def test_get_draft_roundtrip(client, db_session):
    policy = _seed_policy(db_session)
    created = client.post(
        f"/api/v1/policies/{policy.id}/application-drafts",
        json=_draft_payload(),
        headers=_headers(),
    ).json()["data"]

    r = client.get(
        f"/api/v1/policies/application-drafts/{created['draft_id']}",
        headers=_headers(),
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["applicant_name"] == "张三"
    assert body["status"] == "draft"


def test_get_draft_not_found(client, db_session):
    r = client.get(
        "/api/v1/policies/application-drafts/does-not-exist",
        headers=_headers(),
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "DRAFT_NOT_FOUND"


# ---------- 小院隔离 ----------


def test_other_yard_policy_invisible(client, db_session):
    policy = _seed_policy(db_session, yard_id="other-yard")
    r = client.post(
        f"/api/v1/policies/{policy.id}/application-drafts",
        json=_draft_payload(),
        headers=_headers(yard_id="t1"),
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "POLICY_NOT_FOUND"


def test_global_policy_visible_to_yards(client, db_session):
    policy = _seed_policy(db_session, yard_id=None)
    r = client.post(
        f"/api/v1/policies/{policy.id}/application-drafts",
        json=_draft_payload(),
        headers=_headers(yard_id="t1"),
    )
    assert r.status_code == 200, r.text


def test_draft_isolated_between_yards(client, db_session):
    policy = _seed_policy(db_session, yard_id=None)
    created = client.post(
        f"/api/v1/policies/{policy.id}/application-drafts",
        json=_draft_payload(),
        headers=_headers(yard_id="t1"),
    ).json()["data"]

    r = client.get(
        f"/api/v1/policies/application-drafts/{created['draft_id']}",
        headers=_headers(yard_id="other-yard"),
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "DRAFT_NOT_FOUND"


def test_unauthorized_without_token(client, db_session):
    r = client.get("/api/v1/policies/application-drafts/whatever")
    assert r.status_code == 401


# ---------- 服务层直接测试 ----------


def test_status_always_draft_at_model_level(db_session):
    from app.modules.policy import service
    from app.modules.policy.schemas import ApplicationDraftCreate

    policy = _seed_policy(db_session)
    draft = service.create_application_draft(
        db_session,
        "t1",
        "u1",
        policy.id,
        ApplicationDraftCreate(applicant_name="李四"),
    )
    assert draft.status == "draft"
    assert isinstance(draft, PolicyApplication)
    assert draft.form_data == {}
    assert draft.missing_materials == ["身份证复印件", "营业执照", "土地流转合同"]
