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
    "skill_codes": [],
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
