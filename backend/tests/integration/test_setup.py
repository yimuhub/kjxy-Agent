"""初始化向导 API 集成测试（v3.74）：一次性安全闸 + 无默认账号 + 技能/模型落库。"""

import pytest
from app.core.deps import get_db_session
from app.db.base import Base
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'setup-test.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    yield factory
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def api_client(session_factory):
    app = create_app()

    def override_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_db
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


PAYLOAD = {
    "username": "zhangliqun",
    "password": "InitPass2026!",
    "display_name": "张立群",
    "email": "zhang@example.com",
    "phone": "13800138000",
    "yard_name": "张家港水稻科技小院",
    "province": "江苏省",
    "city": "苏州市",
    "county": "张家港市",
    "profile_types": ["rice", "general"],
    "yard_description": "稻麦轮作示范",
    "school": "南京农业大学",
    "industry": "优质稻米",
    # 注意：不传 skill_codes = 老客户端行为（铺全部内置技能）；
    # 传 [] = 用户明确全不勾选（一个都不铺）——见 test_initialize_empty_skill_codes。
    "app_name": "稻香小院",
    "primary_color": "#6366F1",
    "deployment": "desktop",
}


def test_status_uninitialized(api_client):
    r = api_client.get("/api/v1/setup/status")
    assert r.status_code == 200
    assert r.json()["data"]["initialized"] is False


def test_skills_catalog_is_public_and_nonempty(api_client):
    r = api_client.get("/api/v1/setup/skills")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["total"] == len(data["items"]) > 20
    assert all(item["code"] and item["name"] for item in data["items"])
    # 不传画像 → 不做任何推荐预选（前端进入第 4 步时会带上画像重新拉取）
    assert data["recommended_count"] == 0
    assert data["recommended_codes"] == []
    # 分组统计齐备（审查 D-13：搜索 + 分类）
    assert [g["key"] for g in data["groups"]] == ["agri", "growth", "office"]
    assert sum(g["total"] for g in data["groups"]) == data["total"]


def test_skills_catalog_recommends_by_profile(api_client):
    """按产业画像推荐：预选数量远小于全量，且 PPT 专项永不自动预选（D-13）。"""
    r = api_client.get("/api/v1/setup/skills", params={"types": "fruit"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["profile_types"] == ["fruit"]
    assert data["profile_labels"] == ["应时鲜果"]
    assert 0 < data["recommended_count"] < data["total"]
    recommended = set(data["recommended_codes"])
    # PPT 专项（6 个）永远不在预选集合里
    ppt = {i["code"] for i in data["items"] if i["code"].startswith("office_ppt_")}
    assert ppt and not (ppt & recommended)
    # 农技 12 个全在预选里（水果画像的核心能力）
    agri = {i["code"] for i in data["items"] if i["group"] == "agri"}
    assert agri <= recommended
    # 每条技能都带分组与关键词，供前端搜索/分类
    assert all(i["group"] and isinstance(i["keywords"], list) for i in data["items"])


def test_initialize_empty_skill_codes_seeds_none(api_client, session_factory):
    """用户明确全不勾选 → 一个技能都不铺（旧实现会铺全部 35 个，D-13 连带缺陷）。"""
    r = api_client.post(
        "/api/v1/setup/initialize",
        json={**PAYLOAD, "username": "nokills", "skill_codes": []},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["skill_count"] == 0

    db = session_factory()
    try:
        from app.modules.evolution.models import Skill

        assert db.scalars(select(Skill)).all() == []
    finally:
        db.close()


def test_initialize_full_flow(api_client, session_factory):
    """空库 → 初始化 → 账号/小院/技能/状态落库 → 自动登录可用 → 二次 409。"""
    r = api_client.post("/api/v1/setup/initialize", json=PAYLOAD)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["username"] == "zhangliqun" and data["access_token"]
    assert data["yard_name"] == PAYLOAD["yard_name"]
    assert data["skill_count"] > 20
    assert data["model_configured"] is False

    # 二次初始化 → 409
    r2 = api_client.post("/api/v1/setup/initialize", json=PAYLOAD)
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "SETUP_ALREADY_DONE"

    # status 已初始化
    assert api_client.get("/api/v1/setup/status").json()["data"]["initialized"] is True

    # 自动登录 token 可用，角色含平台管理员
    me = api_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {data['access_token']}"},
    )
    assert me.status_code == 200
    me_data = me.json()["data"]
    assert "platform_admin" in me_data["roles"]
    assert me_data["yards"] and me_data["yards"][0]["yard_name"] == PAYLOAD["yard_name"]

    db = session_factory()
    try:
        from app.modules.evolution.models import Skill
        from app.modules.setup.models import SetupState
        from app.modules.yards.settings_service import get_setting

        state = db.scalars(select(SetupState)).first()
        assert state is not None and state.yard_id == data["yard_id"]
        assert state.admin_user_id == data["user_id"]

        skills = db.scalars(select(Skill).where(Skill.yard_id == data["yard_id"])).all()
        assert len(skills) == data["skill_count"] > 20
        assert all(row.status == "published" for row in skills)

        profile = get_setting(db, data["yard_id"], "yard_profile")
        assert "rice" in profile["types"] and "general" in profile["types"]
        assert profile["deployment"] == "desktop"
        assert profile["county"] == "张家港市"
        # v3.78：向导「培养单位/主导产业」必须真正持久化（此前是前端死字段）
        assert profile["unit"] == "南京农业大学"
        assert profile["industry"] == "优质稻米"
    finally:
        db.close()


def test_initialize_can_filter_skills(api_client):
    catalog = api_client.get("/api/v1/setup/skills").json()["data"]["items"]
    code = catalog[0]["code"]
    r = api_client.post(
        "/api/v1/setup/initialize",
        json={**PAYLOAD, "username": "onlyone", "skill_codes": [code]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["skill_count"] == 1


def test_initialize_writes_encrypted_provider(api_client, session_factory):
    r = api_client.post(
        "/api/v1/setup/initialize",
        json={
            **PAYLOAD,
            "username": "modelowner",
            "llm_provider": {
                "provider_type": "deepseek",
                "name": "DeepSeek",
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "sk-secret-should-not-leak",
                "model_id": "deepseek-chat",
            },
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["model_configured"] is True

    db = session_factory()
    try:
        from app.modules.llm.supplier_models import Supplier

        row = db.scalars(select(Supplier)).first()
        assert row is not None
        assert row.api_key_enc
        assert "sk-secret-should-not-leak" not in row.api_key_enc
    finally:
        db.close()


@pytest.mark.parametrize("username", ["admin", "root", "system", "administrator"])
def test_initialize_rejects_weak_username(api_client, username):
    r = api_client.post("/api/v1/setup/initialize", json={**PAYLOAD, "username": username})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "SETUP_WEAK_USERNAME"
    # 未初始化状态未被破坏：仍可正常初始化
    assert api_client.get("/api/v1/setup/status").json()["data"]["initialized"] is False


@pytest.mark.parametrize(
    "password",
    ["short", "aaaaaaaaaaaa", "1234567890", "admin123", "zhangliqun2026"],
)
def test_initialize_rejects_weak_password(api_client, password):
    r = api_client.post("/api/v1/setup/initialize", json={**PAYLOAD, "password": password})
    assert r.status_code in (400, 422), r.text
    assert api_client.get("/api/v1/setup/status").json()["data"]["initialized"] is False


def test_initialize_rejects_short_password_at_schema(api_client):
    """pydantic min_length 先拦，框架统一映射 400；库保持未初始化。"""
    r = api_client.post("/api/v1/setup/initialize", json={**PAYLOAD, "password": "short"})
    assert r.status_code == 400
    r2 = api_client.post("/api/v1/setup/initialize", json=PAYLOAD)
    assert r2.status_code == 200


def test_legacy_user_table_still_counts_as_initialized(api_client, session_factory):
    """存量库没有 setup_state 但有用户：按已初始化处理，不再弹向导。"""
    from app.core.security import hash_password
    from app.modules.auth.models import User

    db = session_factory()
    try:
        db.add(
            User(
                username="legacy_admin",
                password_hash=hash_password("LegacyPass2026!"),
                display_name="老管理员",
                status="active",
                platform_admin=True,
            )
        )
        db.commit()
    finally:
        db.close()

    assert api_client.get("/api/v1/setup/status").json()["data"]["initialized"] is True
    r = api_client.post("/api/v1/setup/initialize", json=PAYLOAD)
    assert r.status_code == 409


def test_initialize_remote_blocked_in_production(session_factory, monkeypatch):
    """生产环境来自非本机的初始化请求默认 403；显式放开后才允许。"""
    from app.core.config import settings

    app = create_app()

    def override_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_db
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "setup_allow_remote", False)
    # 不进入 lifespan：生产启动校验会拒绝 SQLite/弱名单，这里只验证路由层的远程闸门
    remote = TestClient(app, client=("203.0.113.5", 5000), raise_server_exceptions=False)
    try:
        r = remote.post("/api/v1/setup/initialize", json=PAYLOAD)
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "SETUP_LOCAL_ONLY"
        assert remote.get("/api/v1/setup/status").json()["data"]["initialized"] is False

        monkeypatch.setattr(settings, "setup_allow_remote", True)
        ok = remote.post("/api/v1/setup/initialize", json=PAYLOAD)
        assert ok.status_code == 200, ok.text
    finally:
        remote.close()


def test_initialize_seeds_default_published_agents(api_client, session_factory):
    """全新安装后必须自带已发布助手，否则第一次提问直接被拦（D-01/D-14 的小白断点）。

    旧实现：新小院一个助手都没有，对话路由报
    「本小院还没有可用的已发布助手或班组」，小白第一次提问就流失。
    """
    r = api_client.post(
        "/api/v1/setup/initialize",
        json={**PAYLOAD, "username": "withagent"},
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["agent_count"] == 2

    db = session_factory()
    try:
        from app.modules.agents.models import YardAgent

        rows = db.scalars(select(YardAgent).where(YardAgent.yard_id == data["yard_id"])).all()
        assert len(rows) == 2
        assert all(row.status == "published" for row in rows)
        assert all(row.visibility == "public" for row in rows)
        # 平台撰写、只读、低风险：不带任何有副作用的工具
        assert all(row.risk_level == "low" for row in rows)
        assert all(row.allowed_tools == ["knowledge.search"] for row in rows)
        assert all("{{question}}" in row.task_prompt_template for row in rows)
    finally:
        db.close()


def test_default_agents_are_routable_candidates(api_client, session_factory):
    """对话路由必须能把新装小院的默认助手列为候选（否则仍然 404）。"""
    r = api_client.post(
        "/api/v1/setup/initialize",
        json={**PAYLOAD, "username": "routable"},
    )
    assert r.status_code == 200, r.text
    yard_id = r.json()["data"]["yard_id"]

    db = session_factory()
    try:
        from app.modules.agents.supervisor import list_candidates

        cands = list_candidates(db, yard_id, user_id=None)
        assert len(cands) >= 1
        assert all(c["kind"] == "agent" for c in cands)
    finally:
        db.close()


def test_default_agents_seeding_is_idempotent(api_client, session_factory):
    """重复初始化同一个库不会铺出第二份默认助手（幂等）。"""
    from app.modules.setup.default_agents import seed_default_agents

    r = api_client.post(
        "/api/v1/setup/initialize",
        json={**PAYLOAD, "username": "idem"},
    )
    assert r.status_code == 200, r.text
    yard_id = r.json()["data"]["yard_id"]

    db = session_factory()
    try:
        from app.modules.agents.models import YardAgent

        again = seed_default_agents(db, yard_id=yard_id, actor_id="x", request_id="t")
        assert again == 0
        rows = db.scalars(select(YardAgent).where(YardAgent.yard_id == yard_id)).all()
        assert len(rows) == 2
    finally:
        db.close()
