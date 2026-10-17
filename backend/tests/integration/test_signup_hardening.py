"""注册与 bootstrap 加固集成测试（规范 §5.1/§16）：

- SIGNUP_ENABLED=false → 403 SIGNUP_DISABLED；
- 保留管理员用户名（大小写不敏感）→ 400 RESERVED_USERNAME；
- 密码最小长度跟随 settings.password_min_length，生产强制 ≥10；
- platform_admin bootstrap：弱名单非生产放行+success 审计；生产拒绝+denied 审计。
"""

import pytest
from app.core.config import settings
from app.core.errors import AppError
from app.core.rate_limit import RateLimiter
from app.core.security import decode_token
from app.modules.audit.models import AuditLog
from app.modules.auth import repository as auth_repo
from app.modules.auth import service as auth_service
from app.modules.tenants import signup_router

SIGNUP_URL = "/api/v1/tenants/signup"


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """放开同 IP 注册频控，保证测试确定性。"""
    monkeypatch.setattr(signup_router, "signup_limiter", RateLimiter(10_000, 3600))


def _signup(client, username="farmer_01", password="passw0rd-123", name="测试小院"):
    return client.post(
        SIGNUP_URL,
        json={"tenant_name": name, "admin_username": username, "password": password},
    )


def test_signup_disabled_returns_403(client, monkeypatch):
    monkeypatch.setattr(settings, "signup_enabled", False)
    resp = _signup(client)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "SIGNUP_DISABLED"


def test_signup_happy_path(client):
    resp = _signup(client)
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["access_token"]


@pytest.mark.parametrize(
    "reserved", ["admin", "Admin", "ROOT", "system", "Service_Worker", "PLATFORM_ADMIN"]
)
def test_reserved_admin_usernames_rejected(client, reserved):
    resp = _signup(client, username=reserved)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "RESERVED_USERNAME"


def test_password_min_length_follows_settings(client, monkeypatch):
    # 默认 password_min_length=8：7 位被拒
    short = _signup(client, username="farmer_len1", password="a1bcde2")
    assert short.status_code == 400
    assert short.json()["error"]["code"] == "INVALID_REQUEST"

    # 调低配置到 6：6 位可过
    monkeypatch.setattr(settings, "password_min_length", 6)
    ok6 = _signup(client, username="farmer_len2", password="abc123")
    assert ok6.status_code == 201, ok6.text


def test_password_min_length_production_floor_10(client, monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    nine = _signup(client, username="farmer_prod", password="a" * 8 + "1")
    assert nine.status_code == 400
    assert nine.json()["error"]["code"] == "INVALID_REQUEST"

    ten = _signup(client, username="farmer_prod", password="a" * 9 + "1")
    assert ten.status_code == 201, ten.text


# ---------- platform_admin bootstrap 弱名单防御 ----------


def _login(db, username, password="p-2fa-notused"):
    return auth_service.login(db, username, password, None, request_id="req-weak-test")


def _audit_rows(db, username):
    rows = db.query(AuditLog).filter(AuditLog.action == "admin_bootstrap_weak_username").all()
    return [r for r in rows if (r.metadata_json or {}).get("username") == username]


def test_bootstrap_weak_username_allowed_in_dev_with_audit(db_session, monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    auth_repo.create_user(db_session, username="admin", password="p-2fa-notused")
    db_session.commit()

    result = _login(db_session, "admin")
    roles = decode_token(result.access_token, expected_type="access")["roles"]
    assert "platform_admin" in roles

    rows = _audit_rows(db_session, "admin")
    assert rows and rows[-1].result == "success"


def test_bootstrap_weak_username_denied_in_production(db_session, monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    auth_repo.create_user(db_session, username="admin", password="p-2fa-notused")
    db_session.commit()

    with pytest.raises(AppError) as exc:
        _login(db_session, "admin")
    assert exc.value.code == "FORBIDDEN"

    rows = _audit_rows(db_session, "admin")
    assert rows and rows[-1].result == "denied"


def test_bootstrap_non_weak_admin_ok_in_production(db_session, monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "platform_admin_usernames", "ops-zx9k-77")
    auth_repo.create_user(db_session, username="ops-zx9k-77", password="p-2fa-notused")
    db_session.commit()

    result = _login(db_session, "ops-zx9k-77")
    roles = decode_token(result.access_token, expected_type="access")["roles"]
    assert "platform_admin" in roles
    assert _audit_rows(db_session, "ops-zx9k-77") == []
