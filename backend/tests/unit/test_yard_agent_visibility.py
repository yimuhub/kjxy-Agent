"""小院助手可见性（public/private）与派生展示名单元测试。

覆盖（安全断言优先）：
1. 服务层创建可见性判定：管理员缺省 public；成员缺省 private；
   成员显式请求 public → 403 FORBIDDEN 拒绝（静默降级是陷阱）；
2. 读路径过滤：get_published_by_code / list_agents / supervisor.list_candidates
   对 private 一律「public OR created_by=请求者」；
3. to_out 派生 display_name（私有助手 = 原名（<用户名>私有）），
   name 保持干净、visibility/created_by/allowed_tools 出参可见；
4. duplicate 可见性跟随源配置（私有副本不得借复制转公开）。
"""

import pytest
from app.core.errors import AppError
from app.modules.agents import declarative, supervisor
from app.modules.agents.declarative import DeclarativeAgentCreate
from app.modules.agents.models import (
    STATUS_DELETED,
    STATUS_PUBLISHED,
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
    YardAgent,
)
from app.modules.auth.models import User
from app.modules.yards.repository import create_yard

BASE_BODY = {
    "agent_code": "vis_helper",
    "name": "可见性助手",
    "persona_prompt": "你是测试助手。",
    "task_prompt_template": "请回答：{{question}}",
    "allowed_tools": ["knowledge.search"],
    "knowledge_scopes": ["yard"],
}


@pytest.fixture()
def yard(db_session):
    t = create_yard(db_session, name="可见性小院")
    db_session.commit()
    return t


def _req(**overrides) -> DeclarativeAgentCreate:
    return DeclarativeAgentCreate.model_validate({**BASE_BODY, **overrides})


def _mk_agent(
    db, yard_id, *, code, name, created_by, visibility=VISIBILITY_PUBLIC, status=STATUS_PUBLISHED
) -> YardAgent:
    agent = YardAgent(
        yard_id=yard_id,
        agent_code=code,
        name=name,
        persona_prompt="p",
        task_prompt_template="q: {{question}}",
        allowed_tools=["knowledge.search"],
        knowledge_scopes=["yard"],
        risk_level="low",
        status=status,
        visibility=visibility,
        created_by=created_by,
    )
    db.add(agent)
    db.commit()
    return agent


# ---------------------------------------------------------------------------
# 1) 创建可见性判定（服务层）
# ---------------------------------------------------------------------------


def test_member_default_create_is_private(db_session, yard):
    agent = declarative.create_agent(
        db_session,
        yard_id=yard.id,
        actor_id="u-member",
        req=_req(),
        request_id="req-v1",
        roles=["viewer"],
    )
    assert agent.visibility == VISIBILITY_PRIVATE
    assert agent.created_by == "u-member"


def test_admin_default_create_is_public(db_session, yard):
    agent = declarative.create_agent(
        db_session,
        yard_id=yard.id,
        actor_id="u-admin",
        req=_req(),
        request_id="req-v2",
        roles=["yard_admin"],
    )
    assert agent.visibility == VISIBILITY_PUBLIC


def test_member_forcing_public_is_rejected(db_session, yard):
    """成员显式请求 public → 403 拒绝（公开需管理员/审批，不做静默降级）。"""
    with pytest.raises(AppError) as exc:
        declarative.create_agent(
            db_session,
            yard_id=yard.id,
            actor_id="u-member",
            req=_req(visibility="public"),
            request_id="req-v3",
            roles=["viewer"],
        )
    assert exc.value.code == "FORBIDDEN"
    assert exc.value.http_status == 403


def test_admin_can_create_private_explicitly(db_session, yard):
    agent = declarative.create_agent(
        db_session,
        yard_id=yard.id,
        actor_id="u-admin",
        req=_req(visibility="private"),
        request_id="req-v4",
        roles=["yard_admin"],
    )
    assert agent.visibility == VISIBILITY_PRIVATE


def test_bad_visibility_value_rejected_by_schema():
    with pytest.raises(AppError) as exc:
        declarative.parse_create_request({**BASE_BODY, "visibility": "secret"})
    assert exc.value.code == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# 2) 读路径过滤（含关键安全断言：private 不可被他人按 code 路由）
# ---------------------------------------------------------------------------


def test_private_not_routable_by_other_member(db_session, yard):
    """安全断言：他人按 code 直接取 published 行 → None（执行链路拒绝）。"""
    _mk_agent(
        db_session,
        yard.id,
        code="secret_one",
        name="私人助手",
        created_by="u-owner",
        visibility=VISIBILITY_PRIVATE,
    )
    assert (
        declarative.get_published_by_code(
            db_session, yard_id=yard.id, agent_code="secret_one", user_id="u-other"
        )
        is None
    )
    # 无请求者上下文同样 fail-closed
    assert (
        declarative.get_published_by_code(db_session, yard_id=yard.id, agent_code="secret_one")
        is None
    )
    # 创建者本人可路由
    found = declarative.get_published_by_code(
        db_session, yard_id=yard.id, agent_code="secret_one", user_id="u-owner"
    )
    assert found is not None and found.name == "私人助手"


def test_public_routable_by_any_member(db_session, yard):
    _mk_agent(
        db_session,
        yard.id,
        code="open_one",
        name="公开助手",
        created_by="u-admin",
        visibility=VISIBILITY_PUBLIC,
    )
    for uid in ("u-owner", "u-other", None):
        assert (
            declarative.get_published_by_code(
                db_session, yard_id=yard.id, agent_code="open_one", user_id=uid
            )
            is not None
        )


def test_list_agents_hides_other_private_and_deleted(db_session, yard):
    _mk_agent(
        db_session,
        yard.id,
        code="own_priv",
        name="我的私有",
        created_by="u-me",
        visibility=VISIBILITY_PRIVATE,
    )
    _mk_agent(
        db_session,
        yard.id,
        code="other_priv",
        name="他人私有",
        created_by="u-other",
        visibility=VISIBILITY_PRIVATE,
    )
    _mk_agent(
        db_session,
        yard.id,
        code="open_pub",
        name="公开",
        created_by="u-admin",
        visibility=VISIBILITY_PUBLIC,
    )
    _mk_agent(
        db_session,
        yard.id,
        code="gone",
        name="已删",
        created_by="u-admin",
        visibility=VISIBILITY_PUBLIC,
        status=STATUS_DELETED,
    )
    listed = declarative.list_agents(db_session, yard_id=yard.id, user_id="u-me")
    codes = {a.agent_code for a in listed}
    assert codes == {"own_priv", "open_pub"}  # 他人 private 与 deleted 全隐去


def test_list_candidates_visibility_filter(db_session, yard):
    _mk_agent(
        db_session,
        yard.id,
        code="cand_priv",
        name="候选私有",
        created_by="u-owner",
        visibility=VISIBILITY_PRIVATE,
    )
    _mk_agent(
        db_session,
        yard.id,
        code="cand_pub",
        name="候选公开",
        created_by="u-admin",
        visibility=VISIBILITY_PUBLIC,
    )
    other = {c["code"] for c in supervisor.list_candidates(db_session, yard.id, user_id="u-other")}
    assert other == {"cand_pub"}
    owner = {c["code"] for c in supervisor.list_candidates(db_session, yard.id, user_id="u-owner")}
    assert owner == {"cand_priv", "cand_pub"}
    # 无请求者上下文（机器人网关等系统路径）fail-closed：只认 public
    system = {c["code"] for c in supervisor.list_candidates(db_session, yard.id)}
    assert system == {"cand_pub"}


def test_cross_yard_isolation_unchanged(db_session, yard):
    other_yard = create_yard(db_session, name="隔壁小院")
    db_session.commit()
    _mk_agent(
        db_session,
        yard.id,
        code="iso",
        name="隔离",
        created_by="u-x",
        visibility=VISIBILITY_PUBLIC,
    )
    assert (
        declarative.get_published_by_code(
            db_session, yard_id=other_yard.id, agent_code="iso", user_id="u-x"
        )
        is None
    )
    assert declarative.list_agents(db_session, yard_id=other_yard.id, user_id="u-x") == []


# ---------------------------------------------------------------------------
# 3) to_out：display_name / visibility / created_by / allowed_tools
# ---------------------------------------------------------------------------


def test_to_out_display_name_private_and_public(db_session, yard):
    user = User(username="李农户", password_hash="x", display_name="")
    user.id = "u-owner"
    db_session.add(user)
    priv = _mk_agent(
        db_session,
        yard.id,
        code="dn_priv",
        name="水肥助手",
        created_by="u-owner",
        visibility=VISIBILITY_PRIVATE,
    )
    pub = _mk_agent(
        db_session,
        yard.id,
        code="dn_pub",
        name="巡园助手",
        created_by="u-owner",
        visibility=VISIBILITY_PUBLIC,
    )
    out_priv = declarative.to_out(priv, db_session)
    assert out_priv["display_name"] == "水肥助手（李农户私有）"
    assert out_priv["name"] == "水肥助手"  # 干净 name：唯一约束/@ 提及不受后缀污染
    assert out_priv["visibility"] == VISIBILITY_PRIVATE
    assert out_priv["created_by"] == "u-owner"
    assert out_priv["allowed_tools"] == ["knowledge.search"]  # 技能绑定出参保留

    out_pub = declarative.to_out(pub, db_session)
    assert out_pub["display_name"] == "巡园助手"
    assert out_pub["visibility"] == VISIBILITY_PUBLIC


def test_to_out_private_without_user_row_falls_back_honestly(db_session, yard):
    """创建者用户已被删：展示「成员」兜底，不编造用户名。"""
    priv = _mk_agent(
        db_session,
        yard.id,
        code="dn_ghost",
        name="幽灵私有",
        created_by="u-gone",
        visibility=VISIBILITY_PRIVATE,
    )
    assert declarative.to_out(priv, db_session)["display_name"] == "幽灵私有（成员私有）"
    # 不传 db 时不派生用户名，但仍带 visibility（服务层内部出参可用）
    out = declarative.to_out(priv)
    assert out["visibility"] == VISIBILITY_PRIVATE and out["display_name"] == "幽灵私有（成员私有）"


def test_to_out_list_batch_display_names(db_session, yard):
    user = User(username="王技", password_hash="x", display_name="")
    user.id = "u-batch"
    db_session.add(user)
    a = _mk_agent(
        db_session,
        yard.id,
        code="b1",
        name="批一",
        created_by="u-batch",
        visibility=VISIBILITY_PRIVATE,
    )
    b = _mk_agent(
        db_session,
        yard.id,
        code="b2",
        name="批二",
        created_by="u-admin",
        visibility=VISIBILITY_PUBLIC,
    )
    outs = declarative.to_out_list(db_session, [a, b])
    assert [o["display_name"] for o in outs] == ["批一（王技私有）", "批二"]


# ---------------------------------------------------------------------------
# 4) duplicate：可见性跟随源配置
# ---------------------------------------------------------------------------


def test_duplicate_inherits_visibility(db_session, yard):
    src = _mk_agent(
        db_session,
        yard.id,
        code="dup_src",
        name="复制源",
        created_by="u-admin",
        visibility=VISIBILITY_PRIVATE,
    )
    copy = declarative.duplicate_agent(
        db_session,
        yard_id=yard.id,
        actor_id="u-admin",
        agent_id=src.id,
        request_id="req-dup",
        roles=["yard_admin"],
    )
    assert copy.visibility == VISIBILITY_PRIVATE  # 私有副本不得借复制转公开
    assert copy.allowed_tools == src.allowed_tools  # 技能白名单原样存活
