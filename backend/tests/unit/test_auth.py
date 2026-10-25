"""认证单元测试：密码哈希/校验、JWT 编解码、权限映射、登录业务规则。"""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from app.core.errors import AppError
from app.core.security import (
    ALGORITHM,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.modules.audit.models import AuditLog  # noqa: F401  注册模型
from app.modules.auth import repository as auth_repo
from app.modules.auth import service as auth_service
from app.modules.auth.models import User  # noqa: F401  注册模型
from app.modules.tenants import repository as tenant_repo
from app.modules.tenants.models import Tenant, TenantMembership  # noqa: F401  注册模型
from app.modules.tenants.permissions import ALL_PERMISSIONS, PERMISSIONS, has_permission

# ---------- 密码哈希 ----------


def test_hash_and_verify_roundtrip():
    h = hash_password("s3cret-pw")
    assert h != "s3cret-pw"
    assert verify_password("s3cret-pw", h)
    assert not verify_password("wrong", h)


def test_verify_password_bad_hash_returns_false():
    assert verify_password("x", "not-a-valid-hash") is False


# ---------- JWT ----------


def test_jwt_access_roundtrip():
    token = create_access_token(user_id="u1", tenant_id="t1", roles=["tenant_admin"])
    payload = decode_token(token, expected_type="access")
    assert payload["sub"] == "u1"
    assert payload["tenant_id"] == "t1"
    assert payload["roles"] == ["tenant_admin"]
    assert payload["type"] == "access"


def test_jwt_refresh_roundtrip_and_type_check():
    token = create_refresh_token(user_id="u1", tenant_id=None)
    payload = decode_token(token, expected_type="refresh")
    assert payload["sub"] == "u1"
    assert payload["type"] == "refresh"
    # 用 access 期望解 refresh 令牌应拒绝
    with pytest.raises(AppError) as exc:
        decode_token(token, expected_type="access")
    assert exc.value.code == "UNAUTHORIZED"


def test_jwt_expired_token_rejected():
    from app.core.config import settings

    expired = jwt.encode(
        {
            "sub": "u1",
            "type": "access",
            "exp": datetime.now(UTC) - timedelta(seconds=1),
            "iat": datetime.now(UTC) - timedelta(hours=1),
        },
        settings.app_secret_key,
        algorithm=ALGORITHM,
    )
    with pytest.raises(AppError) as exc:
        decode_token(expired, expected_type="access")
    assert exc.value.http_status == 401


def test_jwt_tampered_token_rejected():
    token = create_access_token(user_id="u1", tenant_id=None, roles=[])
    with pytest.raises(AppError) as exc:
        decode_token(token[:-2] + "xx", expected_type="access")
    assert exc.value.code == "UNAUTHORIZED"


# ---------- 权限映射 ----------


def test_platform_admin_has_all_permissions():
    assert PERMISSIONS["platform_admin"] == ALL_PERMISSIONS
    for perm in ALL_PERMISSIONS:
        assert has_permission(["platform_admin"], perm)


def test_tenant_admin_has_all_tenant_permissions():
    for perm in ALL_PERMISSIONS:
        assert has_permission(["tenant_admin"], perm)


def test_role_permission_matrix():
    assert has_permission(["expert"], "review:approve")
    assert not has_permission(["expert"], "members:manage")
    assert not has_permission(["expert"], "tasks:cancel")
    assert has_permission(["operator"], "tasks:retry")
    assert not has_permission(["operator"], "review:approve")
    assert has_permission(["viewer"], "knowledge:read")
    assert not has_permission(["viewer"], "tasks:create")
    assert has_permission(["service_worker"], "tasks:create")
    assert not has_permission(["service_worker"], "knowledge:read")


def test_has_permission_unknown_role_and_multi_roles():
    assert not has_permission(["ghost"], "knowledge:read")
    assert has_permission(["viewer", "operator"], "tasks:retry")
    assert not has_permission([], "knowledge:read")


# ---------- 登录业务规则 ----------


@pytest.fixture()
def seeded(db_session):
    """用户 alice 归属 2 个租户，bob 归属 1 个，carol 无租户，mallory 被停用。"""
    t1 = tenant_repo.create_tenant(db_session, name="小院A")
    t2 = tenant_repo.create_tenant(db_session, name="小院B")
    alice = auth_repo.create_user(
        db_session, username="alice", password="pw-alice", display_name="Alice"
    )
    bob = auth_repo.create_user(db_session, username="bob", password="pw-bob")
    carol = auth_repo.create_user(db_session, username="carol", password="pw-carol")
    mallory = auth_repo.create_user(
        db_session, username="mallory", password="pw-x", status="disabled"
    )
    tenant_repo.add_membership(db_session, tenant_id=t1.id, user_id=alice.id, role="tenant_admin")
    tenant_repo.add_membership(db_session, tenant_id=t2.id, user_id=alice.id, role="viewer")
    tenant_repo.add_membership(db_session, tenant_id=t1.id, user_id=bob.id, role="operator")
    db_session.commit()
    return {"t1": t1, "t2": t2, "alice": alice, "bob": bob, "carol": carol, "mallory": mallory}


def test_login_single_tenant_auto_select(db_session, seeded):
    data = auth_service.login(db_session, "bob", "pw-bob", None, request_id="req_t")
    assert data.user.username == "bob"
    assert len(data.tenants) == 1
    payload = decode_token(data.access_token)
    assert payload["tenant_id"] == seeded["t1"].id
    assert payload["roles"] == ["operator"]
    assert data.refresh_token


def test_login_zero_tenant_still_ok_with_none(db_session, seeded):
    data = auth_service.login(db_session, "carol", "pw-carol", None, request_id="req_t")
    payload = decode_token(data.access_token)
    assert payload["tenant_id"] is None
    assert payload["roles"] == []
    assert data.tenants == []


def test_login_multi_tenant_requires_tenant_id(db_session, seeded):
    with pytest.raises(AppError) as exc:
        auth_service.login(db_session, "alice", "pw-alice", None, request_id="req_t")
    assert exc.value.code == "TENANT_REQUIRED"
    assert exc.value.http_status == 400
    tenants = exc.value.details["tenants"]
    assert {t["tenant_id"] for t in tenants} == {seeded["t1"].id, seeded["t2"].id}


def test_login_multi_tenant_with_valid_tenant(db_session, seeded):
    data = auth_service.login(db_session, "alice", "pw-alice", seeded["t2"].id, request_id="req_t")
    payload = decode_token(data.access_token)
    assert payload["tenant_id"] == seeded["t2"].id
    assert payload["roles"] == ["viewer"]
    assert len(data.tenants) == 2
    # 前端依赖 LoginResponse.roles 与 TenantBrief 字段渲染空间选择与菜单
    assert data.roles == ["viewer"]
    brief = next(t for t in data.tenants if t.tenant_id == seeded["t2"].id)
    assert brief.tenant_name == "小院B"
    assert brief.roles == ["viewer"]


def test_login_forbidden_tenant(db_session, seeded):
    with pytest.raises(AppError) as exc:
        auth_service.login(db_session, "bob", "pw-bob", seeded["t2"].id, request_id="req_t")
    assert exc.value.code == "TENANT_FORBIDDEN"
    assert exc.value.http_status == 403


def test_login_wrong_password(db_session, seeded):
    with pytest.raises(AppError) as exc:
        auth_service.login(db_session, "bob", "nope", None, request_id="req_t")
    assert exc.value.code == "INVALID_CREDENTIALS"
    assert exc.value.http_status == 401


def test_login_disabled_account(db_session, seeded):
    with pytest.raises(AppError) as exc:
        auth_service.login(db_session, "mallory", "pw-x", None, request_id="req_t")
    assert exc.value.code == "ACCOUNT_DISABLED"
    assert exc.value.http_status == 403


def test_login_updates_last_login_at(db_session, seeded):
    assert seeded["bob"].last_login_at is None
    auth_service.login(db_session, "bob", "pw-bob", None, request_id="req_t")
    db_session.refresh(seeded["bob"])
    assert seeded["bob"].last_login_at is not None


def test_refresh_issues_new_access_token(db_session, seeded):
    data = auth_service.login(db_session, "bob", "pw-bob", None, request_id="req_t")
    new = auth_service.refresh(db_session, data.refresh_token, request_id="req_t2")
    payload = decode_token(new.access_token)
    assert payload["sub"] == seeded["bob"].id
    assert payload["tenant_id"] == seeded["t1"].id
    assert payload["roles"] == ["operator"]


def test_refresh_with_access_token_rejected(db_session, seeded):
    data = auth_service.login(db_session, "bob", "pw-bob", None, request_id="req_t")
    with pytest.raises(AppError) as exc:
        auth_service.refresh(db_session, data.access_token, request_id="req_t2")
    assert exc.value.code == "UNAUTHORIZED"


def test_refresh_disabled_user_rejected(db_session, seeded):
    # 先为 mallory 造一个 refresh 令牌（绕过 authenticate），再停用校验
    token = create_refresh_token(user_id=seeded["mallory"].id, tenant_id=None)
    with pytest.raises(AppError) as exc:
        auth_service.refresh(db_session, token, request_id="req_t")
    assert exc.value.code == "ACCOUNT_DISABLED"
