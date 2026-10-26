"""小院助手软删除（DELETE /agents/declarative/{id}）单元测试。

覆盖：
1. yard_admin 删除 → status='deleted'、行保留、审计 agent_deleted；
2. name 确定性改名 ``原名（已删除·<id前8位>）`` 释放同小院唯一名供复用；
3. 所有读路径隐去 deleted：列表/run/候选/PATCH/再次 DELETE 一律不可见（404）；
4. 普通成员删除 → 403（与既有 yard_admin 闸门口径一致）；跨小院 → 404。
"""

import pytest
from app.core.security import create_access_token
from app.modules.agents import declarative, supervisor
from app.modules.agents.models import STATUS_DELETED
from app.modules.audit.models import AuditLog
from app.modules.yards.repository import create_yard

BODY = {
    "agent_code": "del_helper",
    "name": "待删助手",
    "persona_prompt": "你是测试助手。",
    "task_prompt_template": "请回答：{{question}}",
    "allowed_tools": ["knowledge.search"],
    "knowledge_scopes": ["yard"],
}


def _hdr(user_id: str = "u-del-admin", yard_id: str | None = None, roles=("yard_admin",)):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def yard(db_session):
    t = create_yard(db_session, name="删除小院")
    db_session.commit()
    return t


def _create(client, yard, body=None):
    resp = client.post(
        "/api/v1/agents/declarative", json=body or BODY, headers=_hdr(yard_id=yard.id)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_soft_delete_keeps_row_and_audits(client, db_session, yard):
    created = _create(client, yard)
    resp = client.delete(
        f"/api/v1/agents/declarative/{created['id']}", headers=_hdr(yard_id=yard.id)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == STATUS_DELETED
    assert data["name"] == f"待删助手（已删除·{created['id'][:8]}）"
    # 行保留：chat 历史/班组/checkpoint 的引用不悬空
    row = db_session.query(declarative.YardAgent).filter_by(id=created["id"]).first()
    assert row is not None
    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "agent_deleted", AuditLog.resource_id == created["id"])
        .first()
    )
    assert audit is not None and audit.metadata_json["deleted_from_status"] == "draft"


def test_deleted_hidden_from_all_read_paths(client, db_session, yard):
    created = _create(client, yard)
    client.post(
        f"/api/v1/agents/declarative/{created['id']}/publish", headers=_hdr(yard_id=yard.id)
    )
    client.delete(f"/api/v1/agents/declarative/{created['id']}", headers=_hdr(yard_id=yard.id))

    # 列表隐去
    listed = client.get("/api/v1/agents/declarative", headers=_hdr(yard_id=yard.id))
    assert all(a["id"] != created["id"] for a in listed.json()["data"]["agents"])
    # 直接按 code 运行隐去（不触发 LLM）
    run = client.post(
        f"/api/v1/agents/{BODY['agent_code']}/run",
        json={"input": {"question": "还能跑吗"}},
        headers=_hdr(yard_id=yard.id),
    )
    assert run.status_code == 404
    # 总控候选隐去
    cands = supervisor.list_candidates(db_session, yard.id, user_id="u-del-admin")
    assert all(c["id"] != created["id"] for c in cands)
    # PATCH/再次删除 → 404
    assert (
        client.patch(
            f"/api/v1/agents/declarative/{created['id']}",
            json={"name": "复活改名"},
            headers=_hdr(yard_id=yard.id),
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/v1/agents/declarative/{created['id']}", headers=_hdr(yard_id=yard.id)
        ).status_code
        == 404
    )


def test_deleted_name_reusable(client, db_session, yard):
    """确定性改名释放唯一名：原名可再次创建（uq_yard_agents_yard_name 不挡路）。"""
    created = _create(client, yard)
    client.delete(f"/api/v1/agents/declarative/{created['id']}", headers=_hdr(yard_id=yard.id))
    again = client.post(
        "/api/v1/agents/declarative",
        json={**BODY, "agent_code": "del_helper_v2"},
        headers=_hdr(yard_id=yard.id),
    )
    assert again.status_code == 200, again.text
    assert again.json()["data"]["name"] == "待删助手"


def test_delete_requires_yard_admin(client, db_session, yard):
    created = _create(client, yard)
    forbidden = client.delete(
        f"/api/v1/agents/declarative/{created['id']}",
        headers=_hdr("u-member", yard.id, ("viewer",)),
    )
    assert forbidden.status_code == 403


def test_delete_cross_yard_404(client, db_session, yard):
    created = _create(client, yard)
    other = create_yard(db_session, name="别家小院")
    db_session.commit()
    resp = client.delete(
        f"/api/v1/agents/declarative/{created['id']}", headers=_hdr("u-x", other.id)
    )
    assert resp.status_code == 404


def test_deprecated_still_visible_to_admin_list_and_status_vocab(client, db_session, yard):
    """status 词汇单一事实源：VALID_STATUSES 含 deleted；deprecated 仍可被管理页列出。"""
    from app.modules.agents.models import VALID_STATUSES

    assert STATUS_DELETED in VALID_STATUSES
    created = _create(client, yard)
    client.post(
        f"/api/v1/agents/declarative/{created['id']}/publish", headers=_hdr(yard_id=yard.id)
    )
    client.post(
        f"/api/v1/agents/declarative/{created['id']}/deprecate", headers=_hdr(yard_id=yard.id)
    )
    listed = client.get("/api/v1/agents/declarative", headers=_hdr(yard_id=yard.id))
    ids = {a["id"] for a in listed.json()["data"]["agents"]}
    assert created["id"] in ids  # 停用≠删除：管理页仍可见（既有口径不变）
    # published 判定只认 published：deprecated/deleted 都不进执行链路
    assert (
        declarative.get_published_by_code(
            db_session, yard_id=yard.id, agent_code=BODY["agent_code"], user_id="u-del-admin"
        )
        is None
    )
