"""声明式 Agent 单元测试（M2 / ADR-0005）。

覆盖：
- assess_risk 纯函数各 Branch（宁严勿松：查不到工具/空知识范围/通配/副作用关键词）；
- tenant_admin 建低风险 → 直接发布成功；
- 高风险 → 只能进 pending_platform_review，approve 后 published、reject 退回 draft；
- platform_admin 直接发布任何风险（审计记 approved_by）；
- 跨租户 GET/PATCH/run 一律 404/403；
- 请求体携带 tenant_id → 422；
- PATCH 重算 risk 且 version 不变；发布后改高风险自动降级。
"""

import pytest
from app.core.security import create_access_token
from app.modules.agents.declarative import assess_risk
from app.modules.audit.models import AuditLog
from app.modules.tenants.repository import create_tenant


def _hdr(user_id: str = "u-decl-admin", tenant_id: str | None = None, roles=("tenant_admin",)):
    token = create_access_token(user_id=user_id, tenant_id=tenant_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# assess_risk 纯函数
# ---------------------------------------------------------------------------

_READ_ONLY = lambda code: (  # noqa: E731 - 测试内联查表
    {"risk_level": "read_only", "side_effect": False} if code == "knowledge.search" else None
)


def test_assess_risk_low_basic():
    assert assess_risk(["knowledge.search"], ["tenant"], _READ_ONLY) == "low"
    assert assess_risk([], ["tenant"], _READ_ONLY) == "low"  # 无工具、有范围 = 低风险


def test_assess_risk_empty_scopes_is_high():
    """知识范围为空（或全空白）→ high：检索边界失控。"""
    assert assess_risk(["knowledge.search"], [], _READ_ONLY) == "high"
    assert assess_risk(["knowledge.search"], None, _READ_ONLY) == "high"
    assert assess_risk([], ["  "], _READ_ONLY) == "high"


def test_assess_risk_side_effect_keyword_beats_clean_meta():
    """工具名含 dispatch/submit/control 等副作用词 → high，即便元数据干净。"""
    clean = lambda code: {"risk_level": "read_only", "side_effect": False}  # noqa: E731
    assert assess_risk(["workorder.dispatch"], ["tenant"], clean) == "high"
    assert assess_risk(["report.submit"], ["tenant"], clean) == "high"
    assert assess_risk(["device.control_valve"], ["tenant"], clean) == "high"


def test_assess_risk_unknown_tool_is_high():
    """registry 查不到的工具 → 宁严勿松一律 high。"""
    assert assess_risk(["no_such.tool"], ["tenant"], lambda c: None) == "high"


def test_assess_risk_side_effect_or_write_reversible_meta_is_high():
    def lookup(code: str):
        if code == "valve.open":
            return {"risk_level": "read_only", "side_effect": True}
        if code == "notes.update":
            return {"risk_level": "write_reversible", "side_effect": False}
        if code == "knowledge.search":
            return {"risk_level": "read_only", "side_effect": False}
        return None

    assert assess_risk(["valve.open"], ["tenant"], lookup) == "high"
    assert assess_risk(["notes.update"], ["tenant"], lookup) == "high"


def test_assess_risk_wildcard_is_high():
    """ADR-0005：白名单服务端硬校验禁通配。"""
    assert assess_risk(["*"], ["tenant"], _READ_ONLY) == "high"
    assert assess_risk(["knowledge.*"], ["tenant"], _READ_ONLY) == "high"


def test_assess_risk_default_lookup_knowledge_search_is_low():
    """默认查表（不注入 lookup）：knowledge.search 是 builtin 只读工具。"""
    assert assess_risk(["knowledge.search"], ["tenant"]) == "low"


# ---------------------------------------------------------------------------
# API：创建 / 发布分级 / 审核 / 跨租户 / schema 校验
# ---------------------------------------------------------------------------

LOW_BODY = {
    "agent_code": "crop_helper",
    "name": "种植助手",
    "description": "低风险问答助手",
    "emoji": "🌱",
    "persona_prompt": "你是种植技术咨询助手。",
    "task_prompt_template": "请回答农户问题：{{question}}",
    "allowed_tools": ["knowledge.search"],
    "knowledge_scopes": ["tenant"],
}

HIGH_BODY = {**LOW_BODY, "agent_code": "risky_helper", "knowledge_scopes": []}


@pytest.fixture()
def tenant_a(db_session):
    t = create_tenant(db_session, name="声明式A")
    db_session.commit()
    return t


@pytest.fixture()
def tenant_b(db_session):
    t = create_tenant(db_session, name="声明式B")
    db_session.commit()
    return t


def _create(client, tenant, body=None, roles=("tenant_admin",)):
    resp = client.post(
        "/api/v1/agents/declarative",
        json=body or LOW_BODY,
        headers=_hdr(tenant_id=tenant.id, roles=roles),
    )
    return resp


def test_create_low_risk_draft_with_risk_level(client, tenant_a):
    resp = _create(client, tenant_a)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["risk_level"] == "low"
    assert data["status"] == "draft"  # 红线：创建一律 draft，发布必须人工点击
    assert data["tenant_id"] == tenant_a.id
    assert data["version"] == "1.0.0"
    assert data["created_from"] == "manual"


def test_create_rejects_tenant_id_in_body(client, tenant_a):
    resp = _create(client, tenant_a, {**LOW_BODY, "tenant_id": tenant_a.id})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_requires_tenant_admin(client, tenant_a):
    resp = _create(client, tenant_a, roles=("viewer",))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_create_rejects_missing_placeholder_and_bad_tools(client, tenant_a):
    bad_template = _create(client, tenant_a, {**LOW_BODY, "task_prompt_template": "没有问题占位"})
    assert bad_template.status_code == 400
    assert bad_template.json()["error"]["code"] == "INVALID_REQUEST"

    wildcard = _create(client, tenant_a, {**LOW_BODY, "allowed_tools": ["*"]})
    assert wildcard.status_code == 400


def test_create_conflicts_with_platform_agent_and_duplicate_version(client, tenant_a):
    taken = _create(client, tenant_a, {**LOW_BODY, "agent_code": "agriculture_qa"})
    assert taken.status_code == 409
    assert taken.json()["error"]["code"] == "AGENT_CODE_TAKEN"

    assert _create(client, tenant_a).status_code == 200
    dup = _create(client, tenant_a)
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "AGENT_VERSION_CONFLICT"


def test_publish_low_risk_by_tenant_admin(client, db_session, tenant_a):
    created = _create(client, tenant_a).json()["data"]
    resp = client.post(
        f"/api/v1/agents/declarative/{created['id']}/publish",
        headers=_hdr(tenant_id=tenant_a.id),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "published"
    count = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "agent_published", AuditLog.tenant_id == tenant_a.id)
        .count()
    )
    assert count == 1


def test_high_risk_goes_to_pending_and_approve_publishes(client, db_session, tenant_a):
    created = _create(client, tenant_a, HIGH_BODY).json()["data"]
    assert created["risk_level"] == "high"

    # tenant_admin 无权直接发布高风险
    resp = client.post(
        f"/api/v1/agents/declarative/{created['id']}/publish",
        headers=_hdr(tenant_id=tenant_a.id),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "pending_platform_review"  # 只能进队列，不能发布

    # 租户管理员看不了平台队列
    forbidden = client.get(
        "/api/v1/agents/admin/agent-review-queue", headers=_hdr(tenant_id=tenant_a.id)
    )
    assert forbidden.status_code == 403

    # 平台队列可见 → 批准 → published（审计记 approved_by）
    queue = client.get(
        "/api/v1/agents/admin/agent-review-queue",
        headers=_hdr("u-platform", None, ("platform_admin",)),
    )
    assert queue.status_code == 200
    ids = [a["id"] for a in queue.json()["data"]["agents"]]
    assert created["id"] in ids

    approve = client.post(
        f"/api/v1/agents/admin/agent-review/{created['id']}/approve",
        headers=_hdr("u-platform", None, ("platform_admin",)),
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["data"]["status"] == "published"
    review = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "agent_reviewed", AuditLog.resource_id == created["id"])
        .first()
    )
    assert review is not None
    assert review.metadata_json["decision"] == "approved"
    assert review.metadata_json["approved_by"] == "u-platform"


def test_high_risk_reject_returns_to_draft(client, db_session, tenant_a):
    created = _create(client, tenant_a, HIGH_BODY).json()["data"]
    client.post(
        f"/api/v1/agents/declarative/{created['id']}/publish", headers=_hdr(tenant_id=tenant_a.id)
    )
    reject = client.post(
        f"/api/v1/agents/admin/agent-review/{created['id']}/reject",
        headers=_hdr("u-platform", None, ("platform_admin",)),
    )
    assert reject.status_code == 200, reject.text
    assert reject.json()["data"]["status"] == "draft"
    review = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "agent_reviewed", AuditLog.resource_id == created["id"])
        .first()
    )
    assert review.metadata_json["decision"] == "rejected"


def test_platform_admin_can_publish_high_directly(client, db_session, tenant_a):
    created = _create(client, tenant_a, HIGH_BODY).json()["data"]
    resp = client.post(
        f"/api/v1/agents/declarative/{created['id']}/publish",
        headers=_hdr("u-platform", tenant_a.id, ("platform_admin",)),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "published"
    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "agent_published", AuditLog.resource_id == created["id"])
        .first()
    )
    assert audit.metadata_json["approved_by"] == "u-platform"


def test_cross_tenant_isolation_patch_and_run(client, tenant_a, tenant_b):
    created = _create(client, tenant_a).json()["data"]

    # 列表隔离
    listed = client.get("/api/v1/agents/declarative", headers=_hdr(tenant_id=tenant_b.id))
    assert listed.status_code == 200
    assert all(a["id"] != created["id"] for a in listed.json()["data"]["agents"])

    # PATCH 跨租户 → 404
    patched = client.patch(
        f"/api/v1/agents/declarative/{created['id']}",
        json={"name": "越权改名"},
        headers=_hdr("u-other", tenant_b.id),
    )
    assert patched.status_code == 404

    # run 跨租户（该 code 只在租户 A 发布）→ 404
    client.post(
        f"/api/v1/agents/declarative/{created['id']}/publish", headers=_hdr(tenant_id=tenant_a.id)
    )
    run = client.post(
        f"/api/v1/agents/{created['agent_code']}/run",
        json={"input": {"question": "水稻怎么种"}},
        headers=_hdr("u-other", tenant_b.id),
    )
    assert run.status_code == 404
    assert run.json()["error"]["code"] == "AGENT_NOT_FOUND"


def test_patch_recomputes_risk_keeps_version_and_downgrades(client, tenant_a):
    created = _create(client, tenant_a).json()["data"]
    client.post(
        f"/api/v1/agents/declarative/{created['id']}/publish", headers=_hdr(tenant_id=tenant_a.id)
    )
    resp = client.patch(
        f"/api/v1/agents/declarative/{created['id']}",
        json={"allowed_tools": ["workorder.dispatch"]},
        headers=_hdr(tenant_id=tenant_a.id),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["risk_level"] == "high"
    assert data["version"] == created["version"]  # 变更记录 version 不变
    # 宁严勿松：已发布配置改成高风险 → 立即退出可运行态进审核队列
    assert data["status"] == "pending_platform_review"

    bad = client.patch(
        f"/api/v1/agents/declarative/{created['id']}",
        json={"tenant_id": tenant_a.id},
        headers=_hdr(tenant_id=tenant_a.id),
    )
    assert bad.status_code == 422


def test_deprecate_exits_runnable_state(client, tenant_a):
    created = _create(client, tenant_a).json()["data"]
    client.post(
        f"/api/v1/agents/declarative/{created['id']}/publish", headers=_hdr(tenant_id=tenant_a.id)
    )
    resp = client.post(
        f"/api/v1/agents/declarative/{created['id']}/deprecate", headers=_hdr(tenant_id=tenant_a.id)
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "deprecated"
    # 停用后平台注册表 miss 且无 published 声明式配置 → 404（不触发 LLM）
    run = client.post(
        f"/api/v1/agents/{created['agent_code']}/run",
        json={"input": {"question": "还能跑吗"}},
        headers=_hdr(tenant_id=tenant_a.id),
    )
    assert run.status_code == 404


def test_draft_agent_not_runnable(client, tenant_a):
    created = _create(client, tenant_a).json()["data"]
    run = client.post(
        f"/api/v1/agents/{created['agent_code']}/run",
        json={"input": {"question": "draft 能跑吗"}},
        headers=_hdr(tenant_id=tenant_a.id),
    )
    assert run.status_code == 404
