"""v3.2 技能可见性与审核闭环集成测试。

覆盖：
- 列表作用域 scope=mine|yard|platform 与基础可见性（普通成员 / 专家 / 管理员 / 负责人）；
- submit_public → pending_review，approve → yard+published，reject → draft 且保持 private；
- 负责人推广 promote（skills:publish_any：默认拒绝、逐人授权后放行、已非私有幂等）；
- service.visible_skill_ids 的语义（None=可见全部，否则 id 集合）。
"""

from __future__ import annotations

import pytest
from app.core.security import create_access_token
from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.evolution import service
from app.modules.evolution.models import Skill
from app.modules.yards.repository import add_membership, create_yard
from sqlalchemy import select

PREFIX = "/api/v1"


# --------------------------------------------------------------------------- 造数
def _user(db, username: str) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash="x",
        display_name=username,
        status="active",
        token_version=0,
    )
    db.add(user)
    db.flush()
    return user


def _hdr(user: User, yard_id: str, role: str) -> dict[str, str]:
    token = create_access_token(user_id=user.id, yard_id=yard_id, roles=[role])
    return {"Authorization": f"Bearer {token}"}


def _skill(
    db,
    *,
    yard_id: str | None,
    code: str,
    version: str = "1.0.0",
    visibility: str = "yard",
    owner_user_id: str | None = None,
    status: str = "draft",
    created_from: str | None = None,
) -> Skill:
    skill = Skill(
        yard_id=yard_id,
        skill_code=code,
        version=version,
        definition_json={"name": code, "summary": f"{code} 摘要"},
        status=status,
        visibility=visibility,
        owner_user_id=owner_user_id,
        created_from=created_from,
    )
    db.add(skill)
    db.flush()
    return skill


@pytest.fixture()
def world(db_session):
    yard = create_yard(db_session, name="可见性测试小院")
    other_yard = create_yard(db_session, name="隔壁小院")
    owner = _user(db_session, "vis_owner")
    admin = _user(db_session, "vis_admin")
    expert = _user(db_session, "vis_expert")
    member = _user(db_session, "vis_member")
    outsider = _user(db_session, "vis_outsider")

    yard.owner_user_id = owner.id
    add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="yard_owner")
    add_membership(db_session, yard_id=yard.id, user_id=admin.id, role="yard_admin")
    add_membership(db_session, yard_id=yard.id, user_id=expert.id, role="expert")
    add_membership(db_session, yard_id=yard.id, user_id=member.id, role="viewer")
    other_yard.owner_user_id = outsider.id
    add_membership(db_session, yard_id=other_yard.id, user_id=outsider.id, role="yard_owner")

    skills = {
        "member_private": _skill(
            db_session,
            yard_id=yard.id,
            code="member_private",
            visibility="private",
            owner_user_id=member.id,
        ),
        "member_yard": _skill(
            db_session,
            yard_id=yard.id,
            code="member_yard",
            visibility="yard",
            owner_user_id=member.id,
            status="published",
        ),
        "owner_private": _skill(
            db_session,
            yard_id=yard.id,
            code="owner_private",
            visibility="private",
            owner_user_id=owner.id,
            status="published",
        ),
        "admin_private": _skill(
            db_session,
            yard_id=yard.id,
            code="admin_private",
            visibility="private",
            owner_user_id=admin.id,
            status="published",
        ),
        "platform": _skill(
            db_session,
            yard_id=None,
            code="builtin_platform",
            visibility="platform",
            status="published",
            created_from="market_builtin",
        ),
        "neighbor_yard": _skill(
            db_session,
            yard_id=other_yard.id,
            code="neighbor_yard",
            visibility="yard",
            status="published",
        ),
    }
    db_session.commit()
    return {
        "yard": yard,
        "other_yard": other_yard,
        "owner": owner,
        "admin": admin,
        "expert": expert,
        "member": member,
        "outsider": outsider,
        "skills": skills,
        "owner_hdr": _hdr(owner, yard.id, "yard_owner"),
        "admin_hdr": _hdr(admin, yard.id, "yard_admin"),
        "expert_hdr": _hdr(expert, yard.id, "expert"),
        "member_hdr": _hdr(member, yard.id, "viewer"),
    }


def _listed_ids(client, headers: dict, **params) -> tuple[set[str], int]:
    resp = client.get(f"{PREFIX}/skills", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return {item["id"] for item in body["data"]}, body["meta"]["total"]


def _review(client, headers: dict, skill_id: str, action: str, note: str | None = None):
    payload: dict = {"action": action}
    if note is not None:
        payload["note"] = note
    return client.post(f"{PREFIX}/skills/{skill_id}/review", json=payload, headers=headers)


# --------------------------------------------------------------------------- 列表与作用域
def test_scope_filters_and_baseline_visibility(client, world):
    sk = world["skills"]

    member_ids, member_total = _listed_ids(client, world["member_hdr"])
    # 普通成员：本小院 yard 级 + 自己的私有；看不到他人私有关、平台级与隔壁小院
    assert member_ids == {sk["member_private"].id, sk["member_yard"].id}
    assert member_total == 2

    yard_ids, _ = _listed_ids(client, world["member_hdr"], scope="yard")
    assert yard_ids == {sk["member_yard"].id}

    mine_ids, mine_total = _listed_ids(client, world["member_hdr"], scope="mine")
    # mine = 自己拥有的全部技能（含私有草稿与自己已发布的小院级）
    assert mine_ids == {sk["member_private"].id, sk["member_yard"].id}
    assert mine_total == 2

    platform_ids, platform_total = _listed_ids(client, world["member_hdr"], scope="platform")
    assert platform_ids == {sk["platform"].id}
    assert platform_total == 1

    # 管理员 / 负责人看得到全部私有（含他人私有），但不含平台级（scope 缺省仍是本小院）
    all_yard = {
        sk["member_private"].id,
        sk["member_yard"].id,
        sk["owner_private"].id,
        sk["admin_private"].id,
    }
    assert _listed_ids(client, world["admin_hdr"])[0] == all_yard
    assert _listed_ids(client, world["owner_hdr"])[0] == all_yard
    # 管理员 scope=yard 仍排除所有私有
    assert _listed_ids(client, world["admin_hdr"], scope="yard")[0] == {sk["member_yard"].id}
    # 管理者的 mine 只含自己创建的行
    assert _listed_ids(client, world["admin_hdr"], scope="mine")[0] == {sk["admin_private"].id}

    # 专家默认看不到他人私有（skills:review 是审核权，不是私有可见权）
    assert _listed_ids(client, world["expert_hdr"])[0] == {sk["member_yard"].id}

    # 非法 scope 由 schema 拦下（仓库统一把参数校验错误映射为 400 INVALID_REQUEST）
    invalid = client.get(f"{PREFIX}/skills", headers=world["member_hdr"], params={"scope": "all"})
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "INVALID_REQUEST"

    # SkillOut 带可见性/归属/审核字段
    resp = client.get(f"{PREFIX}/skills", headers=world["member_hdr"], params={"scope": "mine"})
    item = resp.json()["data"][0]
    assert {
        "visibility",
        "owner_user_id",
        "origin",
        "reviewed_by",
        "reviewed_at",
        "review_note",
    } <= set(item)


# --------------------------------------------------------------------------- 审核闭环
def test_submit_public_then_approve_to_yard(client, db_session, world):
    skill = world["skills"]["member_private"]

    submitted = _review(client, world["member_hdr"], skill.id, "submit_public")
    assert submitted.status_code == 200, submitted.text
    data = submitted.json()["data"]
    assert data["status"] == "pending_review"
    assert data["visibility"] == "private"  # 只是申请公开，审核前不泄漏给全小院
    assert data["reviewed_by"] is None

    db_session.expire_all()
    submit_audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.yard_id == world["yard"].id,
            AuditLog.action == "skill.submit_public",
        )
    )
    assert submit_audit is not None
    assert submit_audit.actor_id == world["member"].id
    assert submit_audit.resource_id == skill.id
    assert submit_audit.metadata_json["visibility"] == "private"

    # 专家无 skills:review 之外的权限？专家有审核权但没有 publish_any，不能代他人提交
    foreign_private = world["skills"]["admin_private"]
    assert (
        _review(client, world["expert_hdr"], foreign_private.id, "submit_public").status_code == 403
    )
    # 普通成员既不是所有者也不是管理者
    assert (
        _review(client, world["member_hdr"], foreign_private.id, "submit_public").status_code == 403
    )
    # 非私有技能不能走 submit_public
    non_private = world["skills"]["member_yard"]
    assert _review(client, world["member_hdr"], non_private.id, "submit_public").status_code == 409
    # 普通成员无 skills:review，不能审核
    assert _review(client, world["member_hdr"], skill.id, "approve").status_code == 403

    approved = _review(client, world["admin_hdr"], skill.id, "approve", "内容可复用")
    assert approved.status_code == 200, approved.text
    data = approved.json()["data"]
    assert data["visibility"] == "yard"
    assert data["status"] == "published"
    assert data["reviewed_by"] == world["admin"].id
    assert data["reviewed_at"] is not None
    assert data["review_note"] == "内容可复用"

    db_session.expire_all()
    approve_audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.yard_id == world["yard"].id,
            AuditLog.action == "skill.approve",
            AuditLog.resource_id == skill.id,
        )
    )
    assert approve_audit is not None
    assert approve_audit.metadata_json["visibility"] == "yard"
    assert approve_audit.metadata_json["skill_code"] == skill.skill_code

    # 公开后普通成员在 yard 作用域能看到它
    yard_ids, _ = _listed_ids(client, world["member_hdr"], scope="yard")
    assert skill.id in yard_ids


def test_submit_public_reject_returns_draft_and_keeps_private(client, db_session, world):
    skill = world["skills"]["member_private"]
    assert _review(client, world["member_hdr"], skill.id, "submit_public").status_code == 200

    rejected = _review(client, world["expert_hdr"], skill.id, "reject", "缺少田间验证数据")
    assert rejected.status_code == 200, rejected.text
    data = rejected.json()["data"]
    assert data["status"] == "draft"
    assert data["visibility"] == "private"
    assert data["review_note"] == "缺少田间验证数据"

    db_session.expire_all()
    stored = db_session.get(Skill, skill.id)
    assert stored is not None
    assert stored.status == "draft"
    assert stored.visibility == "private"
    assert stored.review_note == "缺少田间验证数据"

    reject_audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.yard_id == world["yard"].id,
            AuditLog.action == "skill.reject",
            AuditLog.resource_id == skill.id,
        )
    )
    assert reject_audit is not None
    assert reject_audit.metadata_json["visibility"] == "private"

    # 被拒后仍是私有：其他普通成员在小院库里看不到
    member_ids, _ = _listed_ids(client, world["member_hdr"], scope="yard")
    assert skill.id not in member_ids


# --------------------------------------------------------------------------- 负责人推广
def test_promote_requires_publish_any_and_is_idempotent(client, db_session, world):
    skill = world["skills"]["member_private"]

    # 管理员默认没有 skills:publish_any（保护权限只可逐人下放）
    denied = client.post(f"{PREFIX}/skills/{skill.id}/promote", headers=world["admin_hdr"])
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "FORBIDDEN"

    promoted = client.post(f"{PREFIX}/skills/{skill.id}/promote", headers=world["owner_hdr"])
    assert promoted.status_code == 200, promoted.text
    data = promoted.json()["data"]
    assert data["visibility"] == "yard"
    assert data["status"] == "published"
    assert data["reviewed_by"] == world["owner"].id

    db_session.expire_all()
    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.yard_id == world["yard"].id,
            AuditLog.action == "skill.force_publish",
            AuditLog.resource_id == skill.id,
        )
    )
    assert audit is not None
    assert audit.actor_id == world["owner"].id
    assert audit.metadata_json["owner_user_id"] == world["member"].id
    assert audit.metadata_json["skill_code"] == skill.skill_code
    assert audit.metadata_json["version"] == skill.version

    # 已非 private：幂等返回当前对象，不报错、不重复写审计
    again = client.post(f"{PREFIX}/skills/{skill.id}/promote", headers=world["owner_hdr"])
    assert again.status_code == 200, again.text
    assert again.json()["data"]["id"] == skill.id
    assert again.json()["data"]["visibility"] == "yard"
    db_session.expire_all()
    force_publish_count = len(
        db_session.scalars(
            select(AuditLog).where(
                AuditLog.yard_id == world["yard"].id,
                AuditLog.action == "skill.force_publish",
            )
        ).all()
    )
    assert force_publish_count == 1

    # 负责人逐人下放 skills:publish_any 后，管理员也能推广
    granted = client.put(
        f"{PREFIX}/yards/current/owner/grants/{world['admin'].id}",
        json={"grants": ["skills:publish_any"]},
        headers=world["owner_hdr"],
    )
    assert granted.status_code == 200, granted.text
    assert "skills:publish_any" in granted.json()["data"]["effective_permissions"]

    other = world["skills"]["admin_private"]
    by_admin = client.post(f"{PREFIX}/skills/{other.id}/promote", headers=world["admin_hdr"])
    assert by_admin.status_code == 200, by_admin.text
    assert by_admin.json()["data"]["visibility"] == "yard"
    assert by_admin.json()["data"]["reviewed_by"] == world["admin"].id


# --------------------------------------------------------------------------- 绑定可见性集合
def test_visible_skill_ids_semantics(db_session, world):
    sk = world["skills"]
    yard_id = world["yard"].id

    # 管理者 → None（可见全部）
    assert service.visible_skill_ids(db_session, yard_id, world["owner"].id, ["yard_owner"]) is None
    assert service.visible_skill_ids(db_session, yard_id, world["admin"].id, ["yard_admin"]) is None
    assert service.visible_skill_ids(db_session, yard_id, "u-ghost", ["platform_admin"]) is None

    visible = service.visible_skill_ids(db_session, yard_id, world["member"].id, ["viewer"])
    assert visible == {sk["member_private"].id, sk["member_yard"].id, sk["platform"].id}

    expert_visible = service.visible_skill_ids(db_session, yard_id, world["expert"].id, ["expert"])
    assert expert_visible == {sk["member_yard"].id, sk["platform"].id}
    assert sk["admin_private"].id not in expert_visible
    assert sk["neighbor_yard"].id not in expert_visible
