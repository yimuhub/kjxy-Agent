"""总控问答入口测试（W5b）：规则→LLM→兜底三级路由 + 审计 + 跨租户隔离。

全部离线（fake provider；LLM 分类用脚本化回放注入 JSON）。
"""

import pytest
from app.core.security import create_access_token
from app.modules.audit.models import AuditLog
from app.modules.llm import providers_fake
from app.modules.tenants.repository import create_tenant

SUP_ASK = "/api/v1/agents/supervisor/ask"
SUP_CANDS = "/api/v1/agents/supervisor/candidates"


def _hdr(user_id: str = "u-sup", tenant_id: str | None = None, roles=("tenant_admin",)):
    token = create_access_token(user_id=user_id, tenant_id=tenant_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _script_isolation():
    providers_fake.reset_scripted_tool_responses()
    yield
    providers_fake.reset_scripted_tool_responses()


@pytest.fixture()
def tenant(db_session):
    t = create_tenant(db_session, name="总控租户")
    db_session.commit()
    return t


@pytest.fixture()
def other_tenant(db_session):
    t = create_tenant(db_session, name="隔壁总控租户")
    db_session.commit()
    return t


def _publish(client, tenant, code: str, name: str, description: str) -> str:
    created = client.post(
        "/api/v1/agents/declarative",
        json={
            "agent_code": code,
            "name": name,
            "description": description,
            "persona_prompt": f"你是{name}。",
            "task_prompt_template": "回答：{{question}}",
            "knowledge_scopes": ["tenant"],
            "allowed_tools": [],
        },
        headers=_hdr(tenant_id=tenant.id),
    )
    assert created.status_code == 200, created.text
    aid = created.json()["data"]["id"]
    pub = client.post(
        f"/api/v1/agents/declarative/{aid}/publish", headers=_hdr(tenant_id=tenant.id)
    )
    assert pub.status_code == 200, pub.text
    return aid


def test_rules_routing_hits_keyword_in_candidate_text(client, tenant):
    _publish(client, tenant, "irrig_a", "灌溉顾问", "负责灌溉浇水与水肥安排")
    _publish(client, tenant, "greenhouse_a", "棚室管家", "棚室通风保温与光照管理")
    resp = client.post(
        SUP_ASK,
        json={"question": "今天地里需要浇水吗"},
        headers=_hdr(tenant_id=tenant.id),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["routed_by"] == "rules"
    assert data["choice"]["name"] == "灌溉顾问"
    assert data["result"]["kind"] == "agent"


def test_llm_routing_used_when_no_keyword(client, tenant):
    aid = _publish(client, tenant, "policy_a", "政策助手", "农业补贴与申报咨询")
    providers_fake.set_scripted_tool_responses(
        [{"content": f'{{"id": "{aid}", "reason": "补贴类问题归政策助手"}}'}]
    )
    resp = client.post(
        SUP_ASK, json={"question": "帮我拿个主意"}, headers=_hdr(tenant_id=tenant.id)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["routed_by"] == "llm"
    assert data["choice"]["name"] == "政策助手"
    assert "政策" in data["reason"]


def test_garbage_llm_output_falls_back_honestly(client, tenant):
    _publish(client, tenant, "any_a", "某助手", "通用问答")
    providers_fake.set_scripted_tool_responses([{"content": "我说不好，你看着办"}])
    resp = client.post(SUP_ASK, json={"question": "随便聊聊"}, headers=_hdr(tenant_id=tenant.id))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["routed_by"] == "fallback"
    assert data["reason"]  # 兜底必须带可见说明，绝不伪装成智能选择


def test_team_candidate_selected_and_executed(client, tenant):
    m1 = _publish(client, tenant, "reader_a", "解读员", "解读棚室读数")
    m2 = _publish(client, tenant, "doctor_a", "诊断员", "棚室问题诊断")
    team = client.post(
        "/api/v1/agents/teams",
        json={
            "name": "棚室管家班组",
            "description": "棚室通风与温湿度问题协作",
            "members": [
                {"agent_id": m1, "role_name": "解读员"},
                {"agent_id": m2, "role_name": "诊断员", "is_critic": True},
            ],
        },
        headers=_hdr(tenant_id=tenant.id),
    )
    assert team.status_code == 200, team.text
    resp = client.post(
        SUP_ASK,
        json={"question": "棚室现在要不要通风"},
        headers=_hdr(tenant_id=tenant.id),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["choice"]["kind"] == "team"
    assert data["result"]["status"] == "completed"
    assert data["result"]["answer"]


def test_cross_tenant_candidates_isolated(client, tenant, other_tenant):
    _publish(client, tenant, "mine_a", "本租户助手", "灌溉类描述")
    _publish(client, other_tenant, "theirs_a", "别人助手", "通用描述")
    resp = client.get(SUP_CANDS, headers=_hdr(tenant_id=tenant.id))
    names = [c["name"] for c in resp.json()["data"]["candidates"]]
    assert "本租户助手" in names and "别人助手" not in names


def test_member_role_can_ask_and_audit_has_routed_by(client, db_session, tenant):
    from sqlalchemy import select

    _publish(client, tenant, "audit_a", "审计测试助手", "灌溉浇水建议")
    question = "现在浇水行不行"
    resp = client.post(
        SUP_ASK, json={"question": question}, headers=_hdr(roles=("operator",), tenant_id=tenant.id)
    )
    assert resp.status_code == 200, resp.text  # 总控问答是成员级能力，无需管理权
    row = db_session.scalars(select(AuditLog).where(AuditLog.action == "supervisor_route")).one()
    md = row.metadata_json or {}
    assert md["routed_by"] == "rules"
    assert "question_digest" in md
    assert question not in str(md)  # 问题全文绝不入审计
