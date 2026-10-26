"""政策模块集成测试：采集 → 列表（小院隔离/过期/状态过滤）→ 匹配全链路。"""

from datetime import UTC, datetime, timedelta

from app.core.security import create_access_token

YARD_1 = "yard-1"
YARD_2 = "yard-2"
USER_1 = "user-1"


def _token(user_id: str, yard_id: str) -> str:
    return create_access_token(user_id=user_id, yard_id=yard_id, roles=["operator"])


def _headers(yard_id: str = YARD_1) -> dict:
    return {"Authorization": f"Bearer {_token(USER_1, yard_id)}"}


def _payload(**kw) -> dict:
    base = {
        "policy_name": "水稻规模化种植补贴",
        "publish_department": "农业农村局",
        "scope": "province",
        "province": "湖南",
        "policy_type": "种植补贴",
        "apply_conditions_json": {
            "crops": ["水稻"],
            "min_area_mu": 50,
            "applicant_types": ["合作社"],
        },
        "material_requirements_json": {"materials": ["营业执照"]},
        "content": "补贴原文内容……",
        "source_url": "https://example.gov.cn/policy/1",
    }
    base.update(kw)
    return base


def test_ingest_then_list_then_match(client):
    # 采集
    resp = client.post("/api/v1/policies", json=_payload(), headers=_headers())
    assert resp.status_code == 201, resp.text
    policy_id = resp.json()["data"]["id"]
    assert resp.json()["data"]["source_hash"]

    # 去重：同小院同名同机构 → 409
    dup = client.post("/api/v1/policies", json=_payload(), headers=_headers())
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "POLICY_DUPLICATE"

    # 他小院采集同名同机构 → 允许
    other = client.post("/api/v1/policies", json=_payload(), headers=_headers(YARD_2))
    assert other.status_code == 201

    # 列表：本小院只看到自己的一条
    listing = client.get("/api/v1/policies", headers=_headers())
    assert listing.status_code == 200
    names = [i["policy_name"] for i in listing.json()["data"]]
    assert names == ["水稻规模化种植补贴"]
    assert listing.json()["meta"]["total"] == 1

    # 他小院列表：只看到他自己的一条
    listing2 = client.get("/api/v1/policies", headers=_headers(YARD_2))
    assert listing2.json()["meta"]["total"] == 1

    # 详情：他小院拿本小院 id → 404
    not_found = client.get(f"/api/v1/policies/{policy_id}", headers=_headers(YARD_2))
    assert not_found.status_code == 404

    # 匹配：全命中 → eligible
    match = client.post(
        "/api/v1/policies/match",
        json={
            "crops": ["水稻"],
            "area_mu": 100,
            "province": "湖南",
            "applicant_type": "合作社",
        },
        headers=_headers(),
    )
    assert match.status_code == 200
    results = match.json()["data"]
    assert len(results) == 1
    assert results[0]["match_level"] == "eligible"
    assert results[0]["verification_status"] == "pending"
    assert "success" not in str(results).lower()  # 不做成功率预测


def test_expired_auto_mark_and_status_filter(client):
    past = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    payload = _payload(policy_name="已过期补贴", apply_end_at=past)
    resp = client.post("/api/v1/policies", json=payload, headers=_headers())
    assert resp.status_code == 201

    # GET list 自动把过期政策标为 expired
    listing = client.get("/api/v1/policies", headers=_headers())
    item = listing.json()["data"][0]
    assert item["status"] == "expired"

    active = client.get("/api/v1/policies?status=active", headers=_headers())
    assert active.json()["meta"]["total"] == 0

    expired = client.get("/api/v1/policies?status=expired", headers=_headers())
    assert expired.json()["meta"]["total"] == 1


def test_match_insufficient_profile(client):
    client.post("/api/v1/policies", json=_payload(), headers=_headers())
    match = client.post(
        "/api/v1/policies/match",
        json={"crops": ["水稻"], "area_mu": 100},  # 缺 province / applicant_type
        headers=_headers(),
    )
    assert match.status_code == 200
    r = match.json()["data"][0]
    assert r["match_level"] == "insufficient"
    assert r["match_level"] != "eligible"
    assert set(r["unknown_conditions"]) == {"province", "applicant_types"}
