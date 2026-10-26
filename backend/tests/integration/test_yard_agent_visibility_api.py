"""小院助手可见性集成测试：HTTP 读/运行路径的 private 边界与派生展示名。

安全断言（核心）：私有助手对其他成员不可路由——包括按 code 的直接运行请求、
总控候选列表与列表接口；创建者本人不受影响。
"""

import pytest
from app.core.security import create_access_token
from app.modules.agents import declarative
from app.modules.agents.models import (
    STATUS_PUBLISHED,
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
    YardAgent,
)
from app.modules.auth.models import User
from app.modules.yards.repository import create_yard

ADMIN = "u-vis-admin"
OWNER = "u-vis-owner"
OTHER = "u-vis-other"


def _hdr(user_id: str, yard_id: str, roles=("yard_admin",)):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def yard(db_session):
    t = create_yard(db_session, name="可见性集成院")
    db_session.commit()
    return t


@pytest.fixture()
def owner_user(db_session):
    u = User(username="赵小院", password_hash="x", display_name="")
    u.id = OWNER
    db_session.add(u)
    db_session.commit()
    return u


def _mk_published(db, yard_id, *, code, name, created_by, visibility):
    agent = YardAgent(
        yard_id=yard_id,
        agent_code=code,
        name=name,
        persona_prompt="你是测试助手。",
        task_prompt_template="请回答：{{question}}",
        allowed_tools=["knowledge.search"],
        knowledge_scopes=["yard"],
        risk_level="low",
        status=STATUS_PUBLISHED,
        visibility=visibility,
        created_by=created_by,
    )
    db.add(agent)
    db.commit()
    return agent


def test_private_not_routable_by_other_member_via_api(client, db_session, yard, owner_user):
    _mk_published(
        db_session,
        yard.id,
        code="priv_one",
        name="张三室私有",
        created_by=OWNER,
        visibility=VISIBILITY_PRIVATE,
    )
    # 其他成员按 code 直接运行 → 404（执行链路即安全边界）
    denied = client.post(
        "/api/v1/agents/priv_one/run",
        json={"input": {"question": "越权路由试试"}},
        headers=_hdr(OTHER, yard.id, ("viewer",)),
    )
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "AGENT_NOT_FOUND"

    # 创建者本人可运行
    allowed = client.post(
        "/api/v1/agents/priv_one/run",
        json={"input": {"question": "自己用没问题"}},
        headers=_hdr(OWNER, yard.id, ("yard_admin",)),
    )
    assert allowed.status_code == 200, allowed.text

    # 总控候选：他人看不到，创建者看得到
    c_other = client.get(
        "/api/v1/agents/supervisor/candidates", headers=_hdr(OTHER, yard.id, ("viewer",))
    )
    assert all(c["code"] != "priv_one" for c in c_other.json()["data"]["candidates"])
    c_owner = client.get("/api/v1/agents/supervisor/candidates", headers=_hdr(OWNER, yard.id))
    assert any(c["code"] == "priv_one" for c in c_owner.json()["data"]["candidates"])


def test_list_and_to_out_expose_visibility_fields(client, db_session, yard, owner_user):
    _mk_published(
        db_session,
        yard.id,
        code="pub_one",
        name="公开助手",
        created_by=ADMIN,
        visibility="public",
    )
    _mk_published(
        db_session,
        yard.id,
        code="priv_two",
        name="私人台账",
        created_by=OWNER,
        visibility=VISIBILITY_PRIVATE,
    )
    listed = client.get("/api/v1/agents/declarative", headers=_hdr(ADMIN, yard.id))
    assert listed.status_code == 200
    agents = listed.json()["data"]["agents"]
    by_code = {a["agent_code"]: a for a in agents}
    # 管理员列表：他人私有助手隐去（仅创建者可见可管）
    assert "pub_one" in by_code and "priv_two" not in by_code
    pub = by_code["pub_one"]
    assert pub["visibility"] == "public"
    assert pub["display_name"] == "公开助手"  # 公开 = 原名逐字一致
    assert pub["created_by"] == ADMIN
    assert pub["allowed_tools"] == ["knowledge.search"]  # 技能绑定出参（前端渲染需要）

    own = client.get("/api/v1/agents/declarative", headers=_hdr(OWNER, yard.id))
    own_items = own.json()["data"]["agents"]
    priv = next(a for a in own_items if a["agent_code"] == "priv_two")
    assert priv["display_name"] == "私人台账（赵小院私有）"  # 派生后缀，name 保持干净
    assert priv["name"] == "私人台账"
    assert priv["visibility"] == VISIBILITY_PRIVATE


def test_admin_created_agent_defaults_public_and_rest_create_accepts_visibility(
    client, db_session, yard
):
    body = {
        "agent_code": "vis_api_helper",
        "name": "接口可见性",
        "persona_prompt": "你是测试助手。",
        "task_prompt_template": "请回答：{{question}}",
        "allowed_tools": ["knowledge.search"],
        "knowledge_scopes": ["yard"],
    }
    created = client.post("/api/v1/agents/declarative", json=body, headers=_hdr(ADMIN, yard.id))
    assert created.status_code == 200
    data = created.json()["data"]
    assert data["visibility"] == "public"
    assert data["display_name"] == "接口可见性"

    priv_body = {
        **body,
        "agent_code": "vis_api_private",
        "name": "接口私有",
        "visibility": "private",
    }
    created_priv = client.post(
        "/api/v1/agents/declarative", json=priv_body, headers=_hdr(ADMIN, yard.id)
    )
    assert created_priv.status_code == 200
    assert created_priv.json()["data"]["visibility"] == VISIBILITY_PRIVATE
    # v2.0 W2 口径：成员可建助手，一律 private（"只能自己使用"）
    member_body = {**body, "agent_code": "vis_api_member", "name": "接口成员私有"}
    member = client.post(
        "/api/v1/agents/declarative", json=member_body, headers=_hdr(OTHER, yard.id, ("viewer",))
    )
    assert member.status_code == 200, member.text
    assert member.json()["data"]["visibility"] == VISIBILITY_PRIVATE
    # 成员显式请求 public → 403（公开需管理员，静默降级是陷阱）
    forced = client.post(
        "/api/v1/agents/declarative",
        json={
            **member_body,
            "agent_code": "vis_api_member_pub",
            "name": "成员要公开",
            "visibility": VISIBILITY_PUBLIC,
        },
        headers=_hdr(OTHER, yard.id, ("viewer",)),
    )
    assert forced.status_code == 403
    assert forced.json()["error"]["code"] == "FORBIDDEN"


def test_service_level_member_path_rejects_forced_public(client, db_session, yard):
    """服务层防线（未来成员创建路径复用）：成员带 roles 显式 public → 403。"""
    from app.core.errors import AppError
    from app.modules.agents.declarative import DeclarativeAgentCreate

    req = DeclarativeAgentCreate.model_validate(
        {
            "agent_code": "svc_priv_guard",
            "name": "服务层守卫",
            "persona_prompt": "p",
            "task_prompt_template": "q：{{question}}",
            "allowed_tools": ["knowledge.search"],
            "knowledge_scopes": ["yard"],
            "visibility": "public",
        }
    )
    with pytest.raises(AppError) as exc:
        declarative.create_agent(
            db_session,
            yard_id=yard.id,
            actor_id=OTHER,
            req=req,
            request_id="req-guard",
            roles=["viewer"],
        )
    assert exc.value.code == "FORBIDDEN"
