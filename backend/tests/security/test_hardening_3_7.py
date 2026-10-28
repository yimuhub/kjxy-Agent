"""3.7 安全加固回归：RBAC 单轨、刷新令牌轮换、登出吊销。

RBAC 双轨的典型症状：JWT 里的旧角色仍能通过 require_permission，
数据库里已经把成员降级为 viewer 也不生效。这里用一个最小探针路由
直接验证 core.deps.require_permission 走 DB 角色覆盖。
"""

import pytest
from app.core.deps import CurrentUser, get_db_session, require_permission
from app.core.security import create_access_token
from app.main import create_app
from app.modules.auth import repository as auth_repo
from app.modules.auth import router as auth_router_module
from app.modules.yards import repository as yard_repo
from fastapi import Depends
from fastapi.testclient import TestClient

PREFIX = "/api/v1"


@pytest.fixture()
def probe_client(db_session):
    # 用 create_app 以复用 AppError 等统一异常处理
    app = create_app()

    @app.get("/probe-write", include_in_schema=False)
    def probe(user: CurrentUser = Depends(require_permission("knowledge:write"))):
        return {"user": user.user_id}

    def override_db():
        yield db_session

    app.dependency_overrides[get_db_session] = override_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def yard_user(db_session):
    yard = yard_repo.create_yard(db_session, name="RBAC 探针小院")
    user = auth_repo.create_user(db_session, username="probe-user", password="pw-probe")
    yard_repo.add_membership(db_session, yard_id=yard.id, user_id=user.id, role="viewer")
    db_session.commit()
    return {"yard": yard, "user": user}


def _token(user, yard, roles: list[str]) -> dict[str, str]:
    token = create_access_token(user_id=user.id, yard_id=yard.id, roles=roles)
    return {"Authorization": f"Bearer {token}"}


def test_db_role_upgrade_applies_to_stale_jwt(probe_client, db_session, yard_user):
    headers = _token(yard_user["user"], yard_user["yard"], ["viewer"])
    assert probe_client.get("/probe-write", headers=headers).status_code == 403

    membership = yard_repo.get_membership(db_session, yard_user["yard"].id, yard_user["user"].id)
    membership.role = "yard_admin"
    db_session.commit()

    # JWT 里仍是 viewer，但 DB 已升级：DB 是唯一权限源
    assert probe_client.get("/probe-write", headers=headers).status_code == 200


def test_db_role_downgrade_overrides_stale_jwt(probe_client, db_session, yard_user):
    membership = yard_repo.get_membership(db_session, yard_user["yard"].id, yard_user["user"].id)
    membership.role = "yard_admin"
    db_session.commit()

    headers = _token(yard_user["user"], yard_user["yard"], ["yard_admin"])
    assert probe_client.get("/probe-write", headers=headers).status_code == 200

    membership.role = "viewer"
    db_session.commit()

    # JWT 里仍是 yard_admin，但 DB 已降级：必须 403，不能靠旧令牌越权
    assert probe_client.get("/probe-write", headers=headers).status_code == 403


def test_platform_admin_without_yard_falls_back_to_jwt(probe_client, db_session):
    admin = auth_repo.create_user(db_session, username="root-ops", password="pw-root")
    db_session.commit()
    token = create_access_token(user_id=admin.id, yard_id=None, roles=["platform_admin"])
    r = probe_client.get("/probe-write", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


@pytest.fixture()
def auth_client(db_session):
    app = create_app()
    app.include_router(auth_router_module.router, prefix=PREFIX)

    def override_db():
        yield db_session

    app.dependency_overrides[get_db_session] = override_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def login_user(db_session):
    yard = yard_repo.create_yard(db_session, name="令牌小院")
    user = auth_repo.create_user(db_session, username="token-user", password="pw-token")
    yard_repo.add_membership(db_session, yard_id=yard.id, user_id=user.id, role="operator")
    db_session.commit()
    return {"yard": yard, "user": user}


def _login(auth_client, yard_id: str) -> dict:
    r = auth_client.post(
        f"{PREFIX}/auth/login",
        json={"username": "token-user", "password": "pw-token", "yard_id": yard_id},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_refresh_rotates_and_old_token_is_rejected(auth_client, login_user):
    first = _login(auth_client, login_user["yard"].id)

    rotated = auth_client.post(
        f"{PREFIX}/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert rotated.status_code == 200, rotated.text
    new_refresh = rotated.json()["data"]["refresh_token"]
    assert new_refresh and new_refresh != first["refresh_token"]

    replay = auth_client.post(
        f"{PREFIX}/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert replay.status_code == 401

    # 旧令牌重放按泄露处理：轮换出的新令牌也被吊销
    after = auth_client.post(f"{PREFIX}/auth/refresh", json={"refresh_token": new_refresh})
    assert after.status_code == 401


def test_logout_revokes_refresh_token(auth_client, login_user):
    data = _login(auth_client, login_user["yard"].id)

    out = auth_client.post(
        f"{PREFIX}/auth/logout",
        json={"refresh_token": data["refresh_token"]},
        headers={"Authorization": f"Bearer {data['access_token']}"},
    )
    assert out.status_code == 200, out.text

    r = auth_client.post(f"{PREFIX}/auth/refresh", json={"refresh_token": data["refresh_token"]})
    assert r.status_code == 401
