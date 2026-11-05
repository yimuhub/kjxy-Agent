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
from app.modules.yards import repository as yard_repo
from app.modules.yards.models import Yard, YardMembership  # noqa: F401  注册模型
from app.modules.yards.permissions import ALL_PERMISSIONS, PERMISSIONS, has_permission

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
    token = create_access_token(user_id="u1", yard_id="t1", roles=["yard_admin"])
    payload = decode_token(token, expected_type="access")
    assert payload["sub"] == "u1"
    assert payload["yard_id"] == "t1"
    assert payload["roles"] == ["yard_admin"]
    assert payload["type"] == "access"


def test_jwt_refresh_roundtrip_and_type_check():
    token = create_refresh_token(user_id="u1", yard_id=None)
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
    token = create_access_token(user_id="u1", yard_id=None, roles=[])
    with pytest.raises(AppError) as exc:
        decode_token(token[:-2] + "xx", expected_type="access")
    assert exc.value.code == "UNAUTHORIZED"


# ---------- 权限映射 ----------


def test_platform_admin_has_all_permissions():
    assert PERMISSIONS["platform_admin"] == ALL_PERMISSIONS
    for perm in ALL_PERMISSIONS:
        assert has_permission(["platform_admin"], perm)


def test_yard_admin_has_operational_permissions_but_not_protected():
    from app.modules.yards.permissions import PROTECTED_PERMISSIONS

    for perm in ALL_PERMISSIONS - PROTECTED_PERMISSIONS:
        assert has_permission(["yard_admin"], perm)
    for perm in PROTECTED_PERMISSIONS:
        assert not has_permission(["yard_admin"], perm)
    # 负责人始终全权限
    for perm in ALL_PERMISSIONS:
        assert has_permission(["yard_owner"], perm)


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
    """用户 alice 归属 2 个小院，bob 归属 1 个，carol 无小院，mallory 被停用。"""
    t1 = yard_repo.create_yard(db_session, name="小院A")
    t2 = yard_repo.create_yard(db_session, name="小院B")
    alice = auth_repo.create_user(
        db_session, username="alice", password="pw-alice", display_name="Alice"
    )
    bob = auth_repo.create_user(db_session, username="bob", password="pw-bob")
    carol = auth_repo.create_user(db_session, username="carol", password="pw-carol")
    mallory = auth_repo.create_user(
        db_session, username="mallory", password="pw-x", status="disabled"
    )
    yard_repo.add_membership(db_session, yard_id=t1.id, user_id=alice.id, role="yard_admin")
    yard_repo.add_membership(db_session, yard_id=t2.id, user_id=alice.id, role="viewer")
    yard_repo.add_membership(db_session, yard_id=t1.id, user_id=bob.id, role="operator")
    db_session.commit()
    return {"t1": t1, "t2": t2, "alice": alice, "bob": bob, "carol": carol, "mallory": mallory}


def test_login_single_yard_auto_select(db_session, seeded):
    data = auth_service.login(db_session, "bob", "pw-bob", None, request_id="req_t")
    assert data.user.username == "bob"
    assert len(data.yards) == 1
    payload = decode_token(data.access_token)
    assert payload["yard_id"] == seeded["t1"].id
    assert payload["roles"] == ["operator"]
    assert data.refresh_token


def test_login_zero_yard_still_ok_with_none(db_session, seeded):
    data = auth_service.login(db_session, "carol", "pw-carol", None, request_id="req_t")
    payload = decode_token(data.access_token)
    assert payload["yard_id"] is None
    assert payload["roles"] == []
    assert data.yards == []


def test_login_multi_yard_requires_yard_id(db_session, seeded):
    with pytest.raises(AppError) as exc:
        auth_service.login(db_session, "alice", "pw-alice", None, request_id="req_t")
    assert exc.value.code == "YARD_REQUIRED"
    assert exc.value.http_status == 400
    yards = exc.value.details["yards"]
    assert {t["yard_id"] for t in yards} == {seeded["t1"].id, seeded["t2"].id}


def test_login_multi_yard_with_valid_yard(db_session, seeded):
    data = auth_service.login(db_session, "alice", "pw-alice", seeded["t2"].id, request_id="req_t")
    payload = decode_token(data.access_token)
    assert payload["yard_id"] == seeded["t2"].id
    assert payload["roles"] == ["viewer"]
    assert len(data.yards) == 2
    # 前端依赖 LoginResponse.roles 与 YardBrief 字段渲染空间选择与菜单
    assert data.roles == ["viewer"]
    brief = next(t for t in data.yards if t.yard_id == seeded["t2"].id)
    assert brief.yard_name == "小院B"
    assert brief.roles == ["viewer"]


def test_login_forbidden_yard(db_session, seeded):
    with pytest.raises(AppError) as exc:
        auth_service.login(db_session, "bob", "pw-bob", seeded["t2"].id, request_id="req_t")
    assert exc.value.code == "YARD_FORBIDDEN"
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
    assert payload["yard_id"] == seeded["t1"].id
    assert payload["roles"] == ["operator"]


def test_refresh_with_access_token_rejected(db_session, seeded):
    data = auth_service.login(db_session, "bob", "pw-bob", None, request_id="req_t")
    with pytest.raises(AppError) as exc:
        auth_service.refresh(db_session, data.access_token, request_id="req_t2")
    assert exc.value.code == "UNAUTHORIZED"


# ---------- 历史 tenant_id claim 兼容（tenant→yard 重命名前签发的在手令牌） ----------


def _legacy_token(**claims) -> str:
    """手工签发一枚重命名前口径的令牌：小院 claim 用旧键 tenant_id。"""
    from app.core.config import settings

    payload = {
        "exp": datetime.now(UTC) + timedelta(days=1),
        "iat": datetime.now(UTC),
        **claims,
    }
    return jwt.encode(payload, settings.app_secret_key, algorithm=ALGORITHM)


def test_refresh_legacy_tenant_claim_keeps_yard_and_roles(db_session, seeded):
    """旧 refresh 令牌（tenant_id）刷新后不得静默降权为空角色。"""
    token = _legacy_token(sub=seeded["bob"].id, tenant_id=seeded["t1"].id, type="refresh")
    new = auth_service.refresh(db_session, token, request_id="req_t")
    payload = decode_token(new.access_token)
    assert payload["yard_id"] == seeded["t1"].id
    assert payload["roles"] == ["operator"]  # 从 DB membership 重新解析，非空


def test_refresh_new_claim_takes_precedence_over_legacy(db_session, seeded):
    """同一令牌新旧键并存时以 yard_id 为准（tenant_id 只是回退）。"""
    token = _legacy_token(
        sub=seeded["alice"].id,
        yard_id=seeded["t2"].id,
        tenant_id=seeded["t1"].id,
        type="refresh",
    )
    new = auth_service.refresh(db_session, token, request_id="req_t")
    payload = decode_token(new.access_token)
    assert payload["yard_id"] == seeded["t2"].id
    assert payload["roles"] == ["viewer"]


def test_refresh_legacy_claim_membership_gone_is_forbidden(db_session, seeded):
    """旧键同样走成员校验：越权旧令牌不能借回退通道放行。"""
    token = _legacy_token(sub=seeded["bob"].id, tenant_id=seeded["t2"].id, type="refresh")
    with pytest.raises(AppError) as exc:
        auth_service.refresh(db_session, token, request_id="req_t")
    assert exc.value.code == "YARD_FORBIDDEN"


def test_resolve_yard_id_claim_single_point():
    """claim 解析唯一入口：新键优先、旧键回退、双缺为 None。"""
    from app.core.security import resolve_yard_id

    assert resolve_yard_id({"yard_id": "y1", "tenant_id": "t1"}) == "y1"
    assert resolve_yard_id({"tenant_id": "t1"}) == "t1"
    assert resolve_yard_id({"yard_id": "y1"}) == "y1"
    assert resolve_yard_id({}) is None


def test_refresh_disabled_user_rejected(db_session, seeded):
    # 先为 mallory 造一个 refresh 令牌（绕过 authenticate），再停用校验
    token = create_refresh_token(user_id=seeded["mallory"].id, yard_id=None)
    with pytest.raises(AppError) as exc:
        auth_service.refresh(db_session, token, request_id="req_t")
    assert exc.value.code == "ACCOUNT_DISABLED"


def test_get_me_returns_hub_context_for_pure_hub_account(db_session, seeded):
    """v3.66：不属任何小院的纯枢纽账号，/auth/me 也返回枢纽上下文与枢纽权限。"""
    from app.core.deps import YardContext
    from app.modules.hubs import service as hub_service

    carol = seeded["carol"]
    admin_ctx = YardContext(
        yard_id=seeded["t1"].id,
        user_id="u-bootstrap",
        roles=["platform_admin"],
        request_id="req",
    )
    hub = hub_service.create_hub(
        db_session, admin_ctx, name="省科协枢纽", mode="province", region="江苏", note=""
    )
    hub_service.add_member(db_session, admin_ctx, hub.id, carol.id, "manager")
    db_session.flush()

    me = auth_service.get_me(db_session, carol.id, roles=[], yard_id=None)
    assert [h.hub_id for h in me.hubs] == [hub.id]
    assert me.hubs[0].mode == "province"
    assert me.hubs[0].role == "manager"
    assert {"hubs:view", "hubs:manage", "hubs:export", "hubs:dispatch"}.issubset(
        set(me.permissions)
    )
