"""审查 B1：跨院账号接管防线（服务层）。

两条独立防线：
1. 挂接已有全局账号须本人确认——create_member / 批量导入不再凭用户名单方面挂接，
   改由邀请链接接受时校验原密码（invitations.accept）。
2. 重置改的是全局 User 凭证——非平台管理员且非本人时，不得重置平台账号或
   跨院共享账号（其他小院仍有效）的全局密码。

用 conftest 的内存 SQLite db_session，直接构造 YardContext 调用服务函数，
不经过登录/权限依赖，聚焦守卫逻辑本身。
"""

from __future__ import annotations

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.security import verify_password
from app.modules.auth.models import User
from app.modules.auth.repository import create_user
from app.modules.yards import member_service
from app.modules.yards import service as yard_service
from app.modules.yards.repository import add_membership, create_yard, get_membership
from app.modules.yards.schemas import MemberCreateRequest


@pytest.fixture()
def world(db_session):
    """院甲：victim 是负责人；院乙：attacker 是管理员、victim 仅是 viewer。"""
    yard_a = create_yard(db_session, name="院甲")
    yard_b = create_yard(db_session, name="院乙")
    victim = create_user(db_session, username="victim", password="Passw0rd123")
    attacker = create_user(db_session, username="attacker", password="Passw0rd123")
    yard_a.owner_user_id = victim.id
    add_membership(db_session, yard_id=yard_a.id, user_id=victim.id, role="yard_owner")
    add_membership(db_session, yard_id=yard_b.id, user_id=attacker.id, role="yard_admin")
    add_membership(db_session, yard_id=yard_b.id, user_id=victim.id, role="viewer")
    db_session.commit()
    return {"a": yard_a, "b": yard_b, "victim": victim, "attacker": attacker}


def _ctx(yard_id: str, user_id: str, roles: list[str]) -> YardContext:
    return YardContext(yard_id=yard_id, user_id=user_id, roles=roles, request_id="test")


def test_reset_blocked_for_cross_yard_shared_account(db_session, world):
    """院乙管理员重置 victim（在院甲仍是负责人）→ 403，全局凭证不变。"""
    victim = world["victim"]
    before_hash = victim.password_hash
    before_ver = int(victim.token_version or 0)
    ctx = _ctx(world["b"].id, world["attacker"].id, ["yard_admin"])

    with pytest.raises(AppError) as exc:
        member_service.reset_member_password(db_session, ctx, victim.id, request_id="t")
    assert exc.value.code == "FORBIDDEN"
    assert exc.value.http_status == 403

    db_session.expire_all()
    row = db_session.get(User, victim.id)
    assert row.password_hash == before_hash
    assert int(row.token_version or 0) == before_ver
    assert verify_password("Passw0rd123", row.password_hash)


def test_reset_blocked_for_platform_account(db_session, world):
    """目标是平台账号（platform_admin 标志）→ 院管理员不得重置其全局密码。"""
    plat = create_user(db_session, username="plat_admin", password="Passw0rd123")
    plat.platform_admin = True
    add_membership(db_session, yard_id=world["b"].id, user_id=plat.id, role="viewer")
    db_session.commit()
    before_hash = plat.password_hash
    ctx = _ctx(world["b"].id, world["attacker"].id, ["yard_admin"])

    with pytest.raises(AppError) as exc:
        member_service.reset_member_password(db_session, ctx, plat.id, request_id="t")
    assert exc.value.code == "FORBIDDEN"
    db_session.expire_all()
    assert db_session.get(User, plat.id).password_hash == before_hash


def test_platform_admin_actor_can_reset_cross_yard(db_session, world):
    """平台管理员（可信、部署级）不受跨院限制，可重置共享账号全局密码。"""
    admin = create_user(db_session, username="root_admin", password="Passw0rd123")
    admin.platform_admin = True
    db_session.commit()
    victim = world["victim"]
    before_ver = int(victim.token_version or 0)
    ctx = _ctx(world["b"].id, admin.id, ["platform_admin"])

    temporary, _member = member_service.reset_member_password(
        db_session, ctx, victim.id, request_id="t"
    )
    assert len(temporary) == 12
    db_session.expire_all()
    row = db_session.get(User, victim.id)
    assert verify_password(temporary, row.password_hash)
    assert not verify_password("Passw0rd123", row.password_hash)
    assert int(row.token_version or 0) == before_ver + 1


def test_self_reset_allowed_even_with_other_memberships(db_session, world):
    """本人重置自己的密码不受跨院限制（user_id == ctx.user_id）。"""
    victim = world["victim"]
    ctx = _ctx(world["b"].id, victim.id, ["viewer"])
    temporary, _member = member_service.reset_member_password(
        db_session, ctx, victim.id, request_id="t"
    )
    db_session.expire_all()
    assert verify_password(temporary, db_session.get(User, victim.id).password_hash)


def test_create_member_existing_account_requires_invite(db_session, world):
    """挂接已有全局账号（非本院成员）→ MEMBER_INVITE_REQUIRED，且不建成员关系。"""
    outsider = create_user(db_session, username="outsider", password="Passw0rd123")
    add_membership(db_session, yard_id=world["a"].id, user_id=outsider.id, role="yard_owner")
    db_session.commit()
    ctx = _ctx(world["b"].id, world["attacker"].id, ["yard_admin"])

    with pytest.raises(AppError) as exc:
        member_service.create_member(
            db_session, ctx, MemberCreateRequest(username="outsider", role="viewer"), request_id="t"
        )
    assert exc.value.code == "MEMBER_INVITE_REQUIRED"
    assert exc.value.http_status == 409
    assert get_membership(db_session, world["b"].id, outsider.id) is None


def test_create_member_new_account_still_works(db_session, world):
    """全新用户名仍可直接建号入组（管理员设初始密码，本人首次登录改密）。"""
    ctx = _ctx(world["b"].id, world["attacker"].id, ["yard_admin"])
    result = member_service.create_member(
        db_session, ctx, MemberCreateRequest(username="brand_new", role="viewer"), request_id="t"
    )
    assert result.initial_password and len(result.initial_password) == 12
    assert result.member.role == "viewer"
    user = db_session.query(User).filter_by(username="brand_new").one()
    assert user.must_change_password is True
    assert get_membership(db_session, world["b"].id, user.id) is not None


def test_batch_add_existing_account_requires_invite(db_session, world):
    """批量导入不再单方面挂接已有账号：逐条返回 MEMBER_INVITE_REQUIRED。"""
    outsider = create_user(db_session, username="outsider2", password="Passw0rd123")
    db_session.commit()
    ctx = _ctx(world["b"].id, world["attacker"].id, ["yard_admin"])

    added, failed = yard_service.batch_add_members(
        db_session, ctx, [("outsider2", "viewer", None), ("ghost", "viewer", None)], request_id="t"
    )
    assert added == []
    codes = {f["username"]: f["error_code"] for f in failed}
    assert codes == {"outsider2": "MEMBER_INVITE_REQUIRED", "ghost": "NOT_FOUND"}
    assert get_membership(db_session, world["b"].id, outsider.id) is None
