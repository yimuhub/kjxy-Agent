"""身份模块单元测试：联系方式脱敏、临时密码、角色层级、待审登录拦截。"""

from __future__ import annotations

import pytest
from app.core.errors import AppError
from app.modules.auth import service as auth_service
from app.modules.auth.repository import create_user
from app.modules.yards.authz import can_manage_target_role
from app.modules.yards.member_service import generate_password, mask_email, mask_phone
from app.modules.yards.signup import normalize_email, normalize_phone


def test_mask_email_and_phone():
    assert mask_email("a@x.com") == "a***@x.com"
    assert mask_email("alice@example.com") == "a***@example.com"
    assert mask_email(None) is None
    assert mask_phone("13812341234") == "138****1234"
    assert mask_phone(None) is None
    assert mask_phone("123") == "***"


def test_generate_password_shape():
    secrets_seen = {generate_password(12) for _ in range(5)}
    assert len(secrets_seen) == 5
    for password in secrets_seen:
        assert len(password) == 12
        # 去掉易混淆字符：不含 0/O/1/l/I
        assert not set(password) & set("0O1lI")


def test_normalize_contact_formats():
    assert normalize_email("  Alice@X.COM ") == "alice@x.com"
    assert normalize_email("") is None
    assert normalize_phone("+86 138-1234-1234") == "13812341234"
    with pytest.raises(AppError):
        normalize_email("not-an-email")
    with pytest.raises(AppError):
        normalize_phone("12345")


def test_role_hierarchy_matrix():
    assert can_manage_target_role("yard_owner", "yard_admin")
    assert can_manage_target_role("yard_owner", "viewer")
    assert not can_manage_target_role("yard_admin", "yard_admin")
    assert can_manage_target_role("yard_admin", "expert")
    assert not can_manage_target_role("admin", "viewer")  # 非合法角色
    assert can_manage_target_role("expert", "operator")
    assert can_manage_target_role("expert", "viewer")
    assert not can_manage_target_role("expert", "expert")
    assert not can_manage_target_role("viewer", "viewer")


def test_authenticate_rejects_pending_account(db_session):
    create_user(db_session, username="pending1", password="Passw0rd123", status="pending")
    db_session.commit()
    with pytest.raises(AppError) as exc:
        auth_service.authenticate(db_session, "pending1", "Passw0rd123")
    assert exc.value.code == "ACCOUNT_PENDING"
    assert exc.value.http_status == 403
