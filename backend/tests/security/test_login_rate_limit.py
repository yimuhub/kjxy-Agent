"""登录限流安全测试。

验证 POST /api/v1/auth/login 的内存滑动窗口限流：
- 失败达到阈值后第 max_attempts+1 次返回 429（code=RATE_LIMITED）
- 成功登录后计数清零
- 不同用户名互不影响

复用 conftest 的 client fixture（create_app 已挂载 auth 路由，prefix /api/v1）。
用 monkeypatch 把路由模块的限流器替换为阈值更小的实例，保持测试快速且独立。
"""

import pytest
from app.core.rate_limit import RateLimiter
from app.modules.auth import repository as auth_repo
from app.modules.auth import router as auth_router_module
from app.modules.auth.models import User  # noqa: F401  注册模型

PREFIX = "/api/v1"
LOGIN = f"{PREFIX}/auth/login"

MAX_ATTEMPTS = 3
WINDOW_SECONDS = 60


@pytest.fixture()
def limited_client(client, monkeypatch):
    """把路由模块的限流器替换为阈值更小的实例，保持测试快速独立。"""
    monkeypatch.setattr(
        auth_router_module,
        "_login_limiter",
        RateLimiter(max_attempts=MAX_ATTEMPTS, window_seconds=WINDOW_SECONDS),
    )
    return client


@pytest.fixture()
def users(db_session):
    auth_repo.create_user(db_session, username="alice", password="pw-alice")
    auth_repo.create_user(db_session, username="bob", password="pw-bob")
    db_session.commit()


def _failed_login(c, username):
    r = c.post(LOGIN, json={"username": username, "password": "wrong-pw"})
    assert r.status_code == 401, r.text
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"
    return r


def test_rate_limited_after_max_failures(limited_client, users):
    """连续失败 max_attempts 次后，第 max_attempts+1 次返回 429。"""
    for _ in range(MAX_ATTEMPTS):
        _failed_login(limited_client, "alice")
    r = limited_client.post(LOGIN, json={"username": "alice", "password": "wrong-pw"})
    assert r.status_code == 429, r.text
    assert r.json()["error"]["code"] == "RATE_LIMITED"


def test_success_resets_counter(limited_client, users):
    """成功登录后计数清零：失败 N-1 次 → 成功一次 → 再失败 N-1 次仍不触发 429。"""
    for _ in range(MAX_ATTEMPTS - 1):
        _failed_login(limited_client, "alice")
    r = limited_client.post(LOGIN, json={"username": "alice", "password": "pw-alice"})
    assert r.status_code == 200, r.text
    # 成功已清零：再失败 MAX_ATTEMPTS 次，第 MAX_ATTEMPTS+1 次才触发 429
    for _ in range(MAX_ATTEMPTS - 1):
        _failed_login(limited_client, "alice")
    r = limited_client.post(LOGIN, json={"username": "alice", "password": "wrong-pw"})
    assert r.status_code == 401, r.text
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_usernames_isolated(limited_client, users):
    """不同用户名互不影响：alice 被限流时 bob 仍可正常登录/失败。"""
    for _ in range(MAX_ATTEMPTS):
        _failed_login(limited_client, "alice")
    r = limited_client.post(LOGIN, json={"username": "alice", "password": "wrong-pw"})
    assert r.status_code == 429, r.text
    assert r.json()["error"]["code"] == "RATE_LIMITED"
    # bob 不受影响：成功登录 200
    r = limited_client.post(LOGIN, json={"username": "bob", "password": "pw-bob"})
    assert r.status_code == 200, r.text
    # bob 的失败仍按自身计数：首次失败是 401 而非 429
    _failed_login(limited_client, "bob")
