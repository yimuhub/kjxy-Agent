"""技能市场种子包单元测试（B1）。

覆盖：
- 种子数据面：14 项、编码唯一且不撞内置、领域分类齐、模板含 {{question}}、
  安全类角色红线内嵌（只读校验，不发请求）；
- GET /market/catalog：字段齐全、risk_level 服务端现算全 low、初始未安装；
- POST /market/install：装载即 seed_ 前缀入库 + 直接 published（自批语义）、
  审计 action=market_install；重复装载全 skipped 不覆盖；
- 请求体带 yard_id → 422；非法 codes → 400；未知编码 → failed；
- 跨小院：B 小院 catalog installed=false，列表看不到 A 的装载结果；
- 权限：非 yard_admin → 403。
"""

import pytest
from app.core.security import create_access_token
from app.modules.audit.models import AuditLog
from app.modules.market.seeds import SEED_AGENTS, SEED_DOMAINS
from app.modules.yards.repository import create_yard

CATALOG = "/api/v1/market/catalog"
INSTALL = "/api/v1/market/install"
DECLARATIVE = "/api/v1/agents/declarative"

#: 任务书指定的 14 个角色编码（不含 seed_ 前缀）
EXPECTED_CODES = {
    "plant_doctor",
    "spray_mixer",
    "greenhouse_keeper",
    "irrigation_advisor",
    "machinery_dispatcher",
    "livestock_helper",
    "market_intel",
    "policy_matcher",
    "agri_copywriter",
    "fertilizer_formulator",
    "soil_reporter",
    "harvest_logger",
    "farm_journal",
    "seedling_scout",
}

#: 数据面三角色（苗情测报/灌溉顾问/棚室管家）额外挂 sensor.query 只读工具
SENSOR_QUERY_SEEDS = {"seedling_scout", "irrigation_advisor", "greenhouse_keeper"}


def _hdr(user_id: str = "u-market-admin", yard_id: str | None = None, roles=("yard_admin",)):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def yard_a(db_session):
    t = create_yard(db_session, name="市场A")
    db_session.commit()
    return t


@pytest.fixture()
def yard_b(db_session):
    t = create_yard(db_session, name="市场B")
    db_session.commit()
    return t


# ---------------------------------------------------------------------------
# 种子数据面（纯断言，不发请求）
# ---------------------------------------------------------------------------


def test_seed_package_shape():
    """14 项、编码唯一且为蛇形、八类领域、模板含占位、建议问题恰好 5 条。"""
    assert len(SEED_AGENTS) == 14
    codes = [s["agent_code"] for s in SEED_AGENTS]
    assert len(set(codes)) == 14
    assert set(codes) == EXPECTED_CODES
    for s in SEED_AGENTS:
        code = s["agent_code"]
        assert code.islower() and code.replace("_", "a").isalnum(), code
        assert s["domain"] in SEED_DOMAINS, code
        # 全部种子至少挂只读检索；三个数据面角色（苗情/灌溉/棚室）追加 sensor.query
        assert s["allowed_tools"][0] == "knowledge.search", code
        expected_extra = ["sensor.query"] if code in SENSOR_QUERY_SEEDS else []
        assert list(s["allowed_tools"][1:]) == expected_extra, code
        assert s["knowledge_scopes"] == ["yard"], code
        assert "{{question}}" in s["task_prompt_template"], code
        assert len(s["suggested_questions"]) == 5, code
        assert s["name"] and s["emoji"] and s["description"], code
        assert s["persona_prompt"].startswith("你是"), code
        # 全角色统一红线：依据检索资料作答、无依据如实说明
        assert "检索" in s["task_prompt_template"], code
        assert "如实" in s["task_prompt_template"] or "不得" in s["task_prompt_template"], code


def test_safety_roles_embed_redlines():
    """安全类角色（初诊/配药/施肥）模板必须内嵌：方向性 + 标签口径 + 禁用限用。"""
    by_code = {s["agent_code"]: s for s in SEED_AGENTS}
    for code in ("plant_doctor", "spray_mixer", "fertilizer_formulator"):
        text = by_code[code]["task_prompt_template"]
        assert "方向性" in text, code
        assert "标签说明" in text, code
    for code in ("plant_doctor", "spray_mixer"):
        assert "禁用、限用" in by_code[code]["task_prompt_template"], code
    # 政策角色：不臆测金额资格
    assert "不臆测" in by_code["policy_matcher"]["task_prompt_template"]


# ---------------------------------------------------------------------------
# GET /market/catalog
# ---------------------------------------------------------------------------

CATALOG_FIELDS = {
    "agent_code",
    "name",
    "emoji",
    "description",
    "domain",
    "suggested_questions",
    "risk_level",
    "installed",
}


def test_catalog_full_fields_all_low_risk_not_installed(client, yard_a):
    resp = client.get(CATALOG, headers=_hdr(yard_id=yard_a.id))
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]["items"]
    assert len(items) == 14
    assert {i["agent_code"] for i in items} == EXPECTED_CODES
    for it in items:
        assert CATALOG_FIELDS <= set(it), it["agent_code"]
        assert it["risk_level"] == "low"  # 服务端 assess_risk 现算：种子全低风险
        assert it["installed"] is False


def test_catalog_requires_yard_admin(client, yard_a):
    resp = client.get(CATALOG, headers=_hdr(yard_id=yard_a.id, roles=("viewer",)))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------
# POST /market/install
# ---------------------------------------------------------------------------


def _install(client, yard, codes, roles=("yard_admin",)):
    return client.post(INSTALL, json={"codes": codes}, headers=_hdr(yard_id=yard.id, roles=roles))


def test_install_creates_published_seed_agents_with_audit(client, db_session, yard_a):
    resp = _install(client, yard_a, ["plant_doctor", "policy_matcher"])
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert set(data["installed"]) == {"plant_doctor", "policy_matcher"}
    assert data["skipped"] == []
    assert data["failed"] == []

    # 声明式列表：seed_ 前缀入库、低风险、直接 published、来源 template
    listed = client.get(DECLARATIVE, headers=_hdr(yard_id=yard_a.id))
    by_code = {a["agent_code"]: a for a in listed.json()["data"]["agents"]}
    assert set(by_code) == {"seed_plant_doctor", "seed_policy_matcher"}
    for agent in by_code.values():
        assert agent["status"] == "published"
        assert agent["risk_level"] == "low"
        assert agent["created_from"] == "template"
        assert agent["yard_id"] == yard_a.id

    # catalog 回填 installed
    catalog = client.get(CATALOG, headers=_hdr(yard_id=yard_a.id)).json()["data"]["items"]
    flags = {i["agent_code"]: i["installed"] for i in catalog}
    assert flags["plant_doctor"] is True and flags["policy_matcher"] is True
    assert flags["spray_mixer"] is False

    # 审计：action=market_install，metadata 记 seed_code 与落库编码
    logs = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "market_install", AuditLog.yard_id == yard_a.id)
        .all()
    )
    assert len(logs) == 2
    seeds_logged = {lg.metadata_json["seed_code"] for lg in logs}
    assert seeds_logged == {"plant_doctor", "policy_matcher"}
    assert all(lg.metadata_json["installed_as"].startswith("seed_") for lg in logs)


def test_install_repeated_skips_without_overwrite(client, db_session, yard_a):
    codes = ["plant_doctor", "spray_mixer", "farm_journal"]
    first = _install(client, yard_a, codes).json()["data"]
    assert len(first["installed"]) == 3

    again = _install(client, yard_a, codes).json()["data"]
    assert again["installed"] == []
    assert set(again["skipped"]) == set(codes)
    assert again["failed"] == []

    # 不覆盖：仍只有 3 行，且名称未被种子重置成任何新值（幂等只增不改）
    listed = client.get(DECLARATIVE, headers=_hdr(yard_id=yard_a.id))
    assert len(listed.json()["data"]["agents"]) == 3

    # 小院自建同码（seed_xxx）同样视为占用：skipped 不覆盖
    client.patch(
        DECLARATIVE + "/" + listed.json()["data"]["agents"][0]["id"],
        json={"name": "小院改名不动"},
        headers=_hdr("u-other-admin", yard_a.id),
    )
    third = _install(client, yard_a, ["plant_doctor"]).json()["data"]
    assert third["skipped"] == ["plant_doctor"]


def test_install_rejects_yard_id_in_body(client, yard_a):
    resp = client.post(
        INSTALL,
        json={"codes": ["plant_doctor"], "yard_id": yard_a.id},
        headers=_hdr(yard_id=yard_a.id),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_install_validation_and_unknown_codes(client, yard_a):
    empty = _install(client, yard_a, [])
    assert empty.status_code == 400 and empty.json()["error"]["code"] == "INVALID_REQUEST"

    too_many = _install(client, yard_a, [f"x{i}" for i in range(21)])
    assert too_many.status_code == 400

    unknown = _install(client, yard_a, ["no_such_seed"])
    assert unknown.status_code == 200
    data = unknown.json()["data"]
    assert data["installed"] == [] and data["skipped"] == []
    assert data["failed"][0]["code"] == "no_such_seed"


def test_install_cross_yard_invisible(client, yard_a, yard_b):
    resp = _install(client, yard_a, ["harvest_logger"])
    assert resp.json()["data"]["installed"] == ["harvest_logger"]

    # B 小院：catalog installed=false，且声明式列表看不到 A 的装载结果
    catalog_b = client.get(CATALOG, headers=_hdr("u-b", yard_b.id)).json()["data"]["items"]
    flags = {i["agent_code"]: i["installed"] for i in catalog_b}
    assert flags["harvest_logger"] is False
    listed_b = client.get(DECLARATIVE, headers=_hdr("u-b", yard_b.id))
    assert listed_b.json()["data"]["agents"] == []

    # B 自行装载同种子：得到的是 B 自己小院的行
    resp_b = _install(client, yard_b, ["harvest_logger"])
    assert resp_b.json()["data"]["installed"] == ["harvest_logger"]
    listed_b2 = client.get(DECLARATIVE, headers=_hdr("u-b", yard_b.id)).json()["data"]["agents"]
    assert len(listed_b2) == 1
    assert listed_b2[0]["yard_id"] == yard_b.id
