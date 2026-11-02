"""小院权限底座单测：负责人保护、管理员边界、角色覆盖、设置校验。"""

import pytest
from app.core.errors import AppError
from app.modules.auth.repository import create_user
from app.modules.yards import roles_service, settings_service
from app.modules.yards.authz import effective_permissions
from app.modules.yards.models import YardMemberGrant
from app.modules.yards.permissions import ALL_PERMISSIONS, PROTECTED_PERMISSIONS
from app.modules.yards.repository import add_membership, create_yard


@pytest.fixture()
def people(db_session):
    yard = create_yard(db_session, name="权限小院")
    owner = create_user(
        db_session, username="owner_u", password="pw-owner-123", display_name="负责人"
    )
    admin = create_user(
        db_session, username="admin_u", password="pw-admin-123", display_name="管理员"
    )
    expert = create_user(
        db_session, username="expert_u", password="pw-expert-123", display_name="专家"
    )
    yard.owner_user_id = owner.id
    add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="yard_owner")
    add_membership(db_session, yard_id=yard.id, user_id=admin.id, role="yard_admin")
    add_membership(db_session, yard_id=yard.id, user_id=expert.id, role="expert")
    db_session.commit()
    return yard, owner, admin, expert


def test_owner_always_has_all_permissions(db_session, people):
    yard, owner, _, _ = people
    perms = effective_permissions(db_session, yard_id=yard.id, user_id=owner.id, jwt_roles=[])
    assert perms == ALL_PERMISSIONS


def test_admin_lacks_protected_permissions(db_session, people):
    yard, _, admin, _ = people
    perms = effective_permissions(db_session, yard_id=yard.id, user_id=admin.id, jwt_roles=[])
    assert "members:manage" in perms
    assert perms & PROTECTED_PERMISSIONS == frozenset()


def test_owner_grant_adds_sensitive_permission_to_admin(db_session, people):
    yard, owner, admin, _ = people
    db_session.add(
        YardMemberGrant(
            yard_id=yard.id,
            user_id=admin.id,
            grants_json=["insights:sensitive", "members:manage_admins"],
            updated_by=owner.id,
        )
    )
    db_session.commit()
    perms = effective_permissions(db_session, yard_id=yard.id, user_id=admin.id, jwt_roles=[])
    assert "insights:sensitive" in perms
    # 不可授予的保护权限被过滤
    assert "members:manage_admins" not in perms


def test_role_override_applies(db_session, people):
    yard, _, admin, _ = people
    roles_service.update_role(
        db_session,
        yard_id=yard.id,
        code="yard_admin",
        name="运营管理员",
        permissions=["knowledge:read", "market:install"],
        actor_id="u-owner",
        request_id="r1",
    )
    db_session.commit()
    perms = effective_permissions(db_session, yard_id=yard.id, user_id=admin.id, jwt_roles=[])
    assert perms == frozenset({"knowledge:read", "market:install"})


def test_custom_role_strips_protected(db_session, people):
    yard, owner, _, _ = people
    out = roles_service.create_custom_role(
        db_session,
        yard_id=yard.id,
        code="agronomist",
        name="农艺师",
        permissions=["knowledge:read", "insights:sensitive", "owner:transfer"],
        actor_id=owner.id,
        request_id="r2",
    )
    db_session.commit()
    assert out["permissions"] == ["knowledge:read"]


def test_owner_role_permissions_immutable(db_session, people):
    yard, owner, _, _ = people
    with pytest.raises(AppError) as e:
        roles_service.update_role(
            db_session,
            yard_id=yard.id,
            code="yard_owner",
            name=None,
            permissions=["knowledge:read"],
            actor_id=owner.id,
            request_id="r3",
        )
    assert e.value.code == "FORBIDDEN"


def test_search_settings_validation_and_key_masking(db_session, people):
    yard, owner, _, _ = people
    settings_service.update_setting(
        db_session,
        yard_id=yard.id,
        key="search",
        value={"provider": "tavily", "tavily_api_key": "tv-secret"},
        actor_id=owner.id,
        request_id="r4",
    )
    db_session.commit()
    view = settings_service.public_view(db_session, yard.id)
    assert view["search"]["provider"] == "tavily"
    assert view["search"]["tavily_configured"] is True
    assert "tavily_api_key" not in view["search"]

    # 单查接口挂的是「逐人授权」的权限点，被授权的成员同样不该看到明文
    single = settings_service.public_setting(db_session, yard.id, "search")
    assert single["tavily_configured"] is True
    assert "tavily_api_key" not in single
    # 运行时自己读配置仍要拿到明文，脱敏只发生在对外出口
    raw = settings_service.get_setting(db_session, yard.id, "search")
    assert raw["tavily_api_key"] == "tv-secret"


def test_search_settings_reject_bad_url(db_session, people):
    yard, owner, _, _ = people
    with pytest.raises(AppError):
        settings_service.update_setting(
            db_session,
            yard_id=yard.id,
            key="search",
            value={"searxng_url": "ftp://example.com"},
            actor_id=owner.id,
            request_id="r5",
        )
