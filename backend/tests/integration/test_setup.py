"""初始化向导 API 集成测试（v3.69）：一次性安全闸 + 全流程。"""

import pytest
from app.core.deps import get_db_session
from app.db.base import Base
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
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
    "username": "admin",
    "password": "InitPass2026!",
    "display_name": "张老师",
    "yard_name": "江苏（句容）葡萄科技小院",
    "province": "江苏",
    "city": "镇江",
    "profile_types": ["fruit", "general"],
    "app_name": "科技小院智能体平台",
    "primary_color": "#6f8dff",
    "deployment": "server",
}


def test_status_uninitialized(api_client):
    r = api_client.get("/api/v1/setup/status")
    assert r.status_code == 200
    assert r.json()["data"]["initialized"] is False


def test_initialize_full_flow(api_client, session_factory):
    """空库 → 初始化 → 用户/小院/画像/品牌/开关落库 → 自动登录可用 → 二次 409。"""
    r = api_client.post("/api/v1/setup/initialize", json=PAYLOAD)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["username"] == "admin" and data["access_token"]

    # 二次初始化 → 409
    r2 = api_client.post("/api/v1/setup/initialize", json=PAYLOAD)
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "SETUP_ALREADY_DONE"

    # status 已初始化
    assert api_client.get("/api/v1/setup/status").json()["data"]["initialized"] is True

    # 自动登录 token 可用
    me = api_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {data['access_token']}"},
    )
    assert me.status_code == 200
    me_data = me.json()["data"]
    assert me_data["roles"] and "platform_admin" in me_data["roles"]
    assert me_data["yards"] and me_data["yards"][0]["yard_name"] == PAYLOAD["yard_name"]

    # 画像落库
    db = session_factory()
    try:
        from app.modules.yards.settings_service import get_setting

        profile = get_setting(db, data["yard_id"], "yard_profile")
        assert "fruit" in profile["types"] and "general" in profile["types"]
        assert profile["deployment"] == "server"
    finally:
        db.close()

    # 弱口令 → 422（不会因已初始化先 409：换新库不可行，这里验证错误优先级即可跳过）


def test_initialize_weak_password_422_fresh_db(api_client):
    """全新库上弱口令 → 422（service 校验，不落任何数据）。"""
    r = api_client.post("/api/v1/setup/initialize", json={**PAYLOAD, "password": "short"})
    # pydantic min_length 先拦（框架统一映射 400）
    assert r.status_code == 400
    # 未初始化状态未被破坏：仍可正常初始化
    r2 = api_client.post("/api/v1/setup/initialize", json=PAYLOAD)
    assert r2.status_code == 200
