"""同小院助手唯一名（DUPLICATE_NAME）单元测试。

覆盖：
- 创建撞名 → 409 DUPLICATE_NAME，details 带既有助手 id/code/name/status；
- 不同小院同名允许；
- 改名撞他人 → 409，改名回自身原名 → 放行（exclude self）；
- 首尾空白不参与比较（"  名  " 与 "名" 视为同名，存储去空白）；
- 模型层 uq_yard_agents_yard_name 约束真实生效（绕过 service 直插 → IntegrityError）。
"""

import pytest
from app.core.security import create_access_token
from app.modules.agents.models import YardAgent
from app.modules.yards.repository import create_yard
from sqlalchemy.exc import IntegrityError

LOW_BODY = {
    "agent_code": "crop_helper",
    "name": "种植助手",
    "description": "低风险问答助手",
    "emoji": "🌱",
    "persona_prompt": "你是种植技术咨询助手。",
    "task_prompt_template": "请回答农户问题：{{question}}",
    "allowed_tools": ["knowledge.search"],
    "knowledge_scopes": ["yard"],
}


def _hdr(user_id: str = "u-name-admin", yard_id: str | None = None, roles=("yard_admin",)):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def yard_a(db_session):
    t = create_yard(db_session, name="唯一名A")
    db_session.commit()
    return t


@pytest.fixture()
def yard_b(db_session):
    t = create_yard(db_session, name="唯一名B")
    db_session.commit()
    return t


def _create(client, yard, body=None):
    return client.post(
        "/api/v1/agents/declarative", json=body or LOW_BODY, headers=_hdr(yard_id=yard.id)
    )


def test_create_collision_returns_409_with_existing_in_details(client, yard_a):
    created = _create(client, yard_a).json()["data"]

    # 不同 agent_code、同名 → 撞名（先过 code 校验，再落 DUPLICATE_NAME）
    resp = _create(client, yard_a, {**LOW_BODY, "agent_code": "other_helper"})
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "DUPLICATE_NAME"
    assert err["details"] == {
        "id": created["id"],
        "agent_code": created["agent_code"],
        "name": created["name"],
        "status": created["status"],
    }


def test_same_name_allowed_in_different_yard(client, yard_a, yard_b):
    assert _create(client, yard_a).status_code == 200
    resp = _create(client, yard_b)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["name"] == LOW_BODY["name"]


def test_rename_collision_and_self_exclusion(client, yard_a):
    first = _create(client, yard_a).json()["data"]
    second = _create(
        client, yard_a, {**LOW_BODY, "agent_code": "second_helper", "name": "第二个"}
    ).json()["data"]

    # 改成别人的名字 → 409 DUPLICATE_NAME（details 指向 first）
    bad = client.patch(
        f"/api/v1/agents/declarative/{second['id']}",
        json={"name": first["name"]},
        headers=_hdr(yard_id=yard_a.id),
    )
    assert bad.status_code == 409
    err = bad.json()["error"]
    assert err["code"] == "DUPLICATE_NAME"
    assert err["details"]["id"] == first["id"]

    # 改名回自身原名（值未变/幂等重放）→ 不算冲突
    ok = client.patch(
        f"/api/v1/agents/declarative/{first['id']}",
        json={"name": first["name"]},
        headers=_hdr(yard_id=yard_a.id),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["name"] == first["name"]


def test_whitespace_stripped_before_compare(client, yard_a):
    assert _create(client, yard_a).status_code == 200
    # 首尾空白不算差异 → 撞名
    resp = _create(client, yard_a, {**LOW_BODY, "agent_code": "ws_helper", "name": "  种植助手  "})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "DUPLICATE_NAME"
    # 存下来的展示名是去空白后的值
    listed = client.get("/api/v1/agents/declarative", headers=_hdr(yard_id=yard_a.id))
    stored = listed.json()["data"]["agents"][0]
    assert stored["name"] == "种植助手"


def test_whitespace_only_name_rejected(client, yard_a):
    resp = _create(client, yard_a, {**LOW_BODY, "name": "   "})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_REQUEST"


def test_unique_constraint_fires_at_db_level(client, db_session, yard_a):
    """绕过 service 直插两行同名（不同 code）→ IntegrityError：
    证明 Base.metadata.create_all 后 uq_yard_agents_yard_name 真实生效。"""

    def _row(code: str):
        return YardAgent(
            yard_id=yard_a.id,
            agent_code=code,
            name="约束直插",
            persona_prompt="p",
            task_prompt_template="{{question}}",
            allowed_tools=[],
            knowledge_scopes=[],
        )

    db_session.add(_row("constraint_a"))
    db_session.flush()
    db_session.add(_row("constraint_b"))
    with pytest.raises(IntegrityError) as excinfo:
        db_session.flush()
    db_session.rollback()
    # SQLite 报错列出的是约束列而非约束名；pg 上由 uq_yard_agents_yard_name 同义约束拦截
    assert "yard_agents.yard_id, yard_agents.name" in str(excinfo.value)
