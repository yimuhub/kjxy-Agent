"""刷新令牌轮换与登出吊销（3.7）。

覆盖：轮换后旧令牌失效、旧令牌重放触发全量吊销、登出精确吊销、
未携带 refresh_token 的旧客户端兼容、v3.7 之前无 jti 的存量令牌可平滑升级。
"""

import pytest
from app.core.errors import AppError
from app.core.security import create_refresh_token
from app.modules.auth import repository as auth_repo
from app.modules.auth import service as auth_service
from app.modules.auth.models import RefreshSession
from app.modules.yards import repository as yard_repo


@pytest.fixture()
def user(db_session):
    t = yard_repo.create_yard(db_session, name="轮换测试小院")
    u = auth_repo.create_user(db_session, username="rotator", password="pw-rotator")
    yard_repo.add_membership(db_session, yard_id=t.id, user_id=u.id, role="operator")
    db_session.commit()
    return u


def _login(db, user) -> str:
    data = auth_service.login(db, "rotator", "pw-rotator", None, request_id="req_login")
    return data.refresh_token


def test_refresh_rotates_and_old_token_dies(db_session, user):
    old = _login(db_session, user)

    first = auth_service.refresh(db_session, old, request_id="req_r1")
    assert first.refresh_token and first.refresh_token != old

    with pytest.raises(AppError) as exc:
        auth_service.refresh(db_session, old, request_id="req_r2")
    assert exc.value.code == "UNAUTHORIZED"


def test_reusing_rotated_token_revokes_all_sessions(db_session, user):
    old = _login(db_session, user)
    rotated = auth_service.refresh(db_session, old, request_id="req_r1").refresh_token

    with pytest.raises(AppError):
        auth_service.refresh(db_session, old, request_id="req_reuse")

    # 泄露处置：用户的全部活跃会话被吊销，新令牌也不能再用
    with pytest.raises(AppError) as exc:
        auth_service.refresh(db_session, rotated, request_id="req_after")
    assert exc.value.code == "UNAUTHORIZED"
    active = (
        db_session.query(RefreshSession)
        .filter(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None))
        .count()
    )
    assert active == 0


def test_logout_revokes_current_refresh_session(db_session, user):
    token = _login(db_session, user)

    auth_service.logout(db_session, user.id, refresh_token=token, request_id="req_logout")

    with pytest.raises(AppError) as exc:
        auth_service.refresh(db_session, token, request_id="req_after_logout")
    assert exc.value.code == "UNAUTHORIZED"


def test_logout_without_token_keeps_session_for_legacy_clients(db_session, user):
    token = _login(db_session, user)

    auth_service.logout(db_session, user.id, request_id="req_logout")

    assert auth_service.refresh(db_session, token, request_id="req_after").access_token


def test_legacy_refresh_token_without_jti_upgrades_gracefully(db_session, user):
    legacy = create_refresh_token(user_id=user.id, yard_id=None)

    upgraded = auth_service.refresh(db_session, legacy, request_id="req_legacy")
    assert upgraded.refresh_token
    assert upgraded.refresh_token != legacy

    # 升级后的令牌进入轮换：再刷一次后旧的新令牌失效
    again = auth_service.refresh(
        db_session, upgraded.refresh_token, request_id="req_rotated"
    ).refresh_token
    assert again != upgraded.refresh_token
    with pytest.raises(AppError):
        auth_service.refresh(db_session, upgraded.refresh_token, request_id="req_old")
