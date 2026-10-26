"""POST /agents/declarative/{id}/duplicate 复制端点单元测试。

覆盖：
- 复制产物名称 ``原名-2``，重复复制依次 ``-3``…（服务端生成，防客户端竞态）；
- 配置字段原样拷贝（persona/模板/工具/知识范围/emoji/supplier_id/description）；
- 新 agent_code 符合 ``^[a-z][a-z0-9_]*$`` 且与源不同；副本一律 draft；
- created_from=template（既有枚举记录复制来源）+ agent_duplicated 审计指回源；
- 闸门：非 yard_admin → 403；跨小院/不存在 → 404。
"""

import re

import pytest
from app.core.security import create_access_token
from app.modules.audit.models import AuditLog
from app.modules.yards.repository import create_yard

BASE_BODY = {
    "agent_code": "dup_helper",
    "name": "复制源",
    "description": "复制测试",
    "emoji": "🧪",
    "persona_prompt": "你是复制测试助手。",
    "task_prompt_template": "请处理：{{question}}",
    "allowed_tools": ["knowledge.search"],
    "knowledge_scopes": ["yard"],
    "supplier_id": "sup-123",
}


def _hdr(user_id: str = "u-dup-admin", yard_id: str | None = None, roles=("yard_admin",)):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def yard_a(db_session):
    t = create_yard(db_session, name="复制A")
    db_session.commit()
    return t


def _create(client, yard, body=None):
    return client.post(
        "/api/v1/agents/declarative", json=body or BASE_BODY, headers=_hdr(yard_id=yard.id)
    )


def _duplicate(client, yard, agent_id):
    return client.post(
        f"/api/v1/agents/declarative/{agent_id}/duplicate", headers=_hdr(yard_id=yard.id)
    )


def test_duplicate_produces_minus2_then_minus3_and_copies_config(client, db_session, yard_a):
    src = _create(client, yard_a).json()["data"]

    first = _duplicate(client, yard_a, src["id"])
    assert first.status_code == 200, first.text
    c1 = first.json()["data"]
    assert c1["name"] == "复制源-2"
    assert c1["status"] == "draft"  # 红线：复制产物同样不进可运行态
    assert c1["agent_code"] != src["agent_code"]
    assert re.fullmatch(r"[a-z][a-z0-9_]*", c1["agent_code"])
    assert c1["created_from"] == "template"  # 既有枚举记录复制来源
    # 配置原样拷贝
    for field in (
        "persona_prompt",
        "task_prompt_template",
        "allowed_tools",
        "knowledge_scopes",
        "emoji",
        "supplier_id",
        "description",
        "risk_level",
        "version",
    ):
        assert c1[field] == src[field], field

    # 手工改名腾位后，第二次复制应得 -3（-2 已被占用）
    occupied = c1["name"]
    assert occupied == "复制源-2"
    second = _duplicate(client, yard_a, src["id"])
    assert second.status_code == 200, second.text
    assert second.json()["data"]["name"] == "复制源-3"

    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "agent_duplicated", AuditLog.resource_id == c1["id"])
        .first()
    )
    assert audit is not None
    assert audit.metadata_json["source_agent_id"] == src["id"]


def test_duplicate_skips_manually_created_colliding_name(client, yard_a):
    src = _create(client, yard_a).json()["data"]
    # 用户手工建了 "复制源-2"（不同 code 不同名不违规，这里占掉 -2 这个名字）
    manual = _create(
        client, yard_a, {**BASE_BODY, "agent_code": "manual_slot", "name": "复制源-2"}
    ).json()["data"]

    dup = _duplicate(client, yard_a, src["id"])
    assert dup.status_code == 200, dup.text
    assert dup.json()["data"]["name"] == "复制源-3"  # -2 已占用 → 自动让位
    # 再复制源一次 → -4（-3 也已被上一条复制占用）
    dup2 = _duplicate(client, yard_a, src["id"])
    assert dup2.json()["data"]["name"] == "复制源-4"
    assert manual["name"] == "复制源-2"


def test_duplicate_requires_yard_admin_and_respects_yard_boundary(client, yard_a):
    src = _create(client, yard_a).json()["data"]

    forbidden = client.post(
        f"/api/v1/agents/declarative/{src['id']}/duplicate",
        headers=_hdr(yard_id=yard_a.id, roles=("viewer",)),
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "FORBIDDEN"

    # 跨小院复制 → 404（不泄露资源存在性）
    missing = client.post(
        f"/api/v1/agents/declarative/{src['id']}/duplicate",
        headers=_hdr(yard_id="yard-other-dup"),
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "AGENT_NOT_FOUND"
