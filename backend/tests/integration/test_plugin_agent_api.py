"""一句话生成插件链路（v3.3）集成测试：生成、对话接线、配额与权限。

全部用例不联网：GitHub 与联网搜索打桩，模型走 fake provider（输出非 JSON →
走确定性兜底 manifest），因此断言可复现。安全性质：插件生成**一律落
pending_review**（不存在"私有即用"），与技能共用 skill_lab 配额与开关。
"""

from __future__ import annotations

import pytest
from app.core.security import create_access_token
from app.modules.audit.models import AuditLog
from app.modules.market import skill_agent
from app.modules.plugins.models import PluginManifest
from app.modules.yards import settings_service
from app.modules.yards.repository import create_yard

BASE = "/api/v1/market"
CHAT = "/api/v1/chat/sessions"
YARD = "yard-plugin-agent"


def _hdr(
    user_id: str = "u-plugin-agent",
    yard_id: str = YARD,
    roles: tuple[str, ...] = ("yard_admin",),
) -> dict[str, str]:
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _reset_quota():
    """与技能共用进程级配额状态，用例间必须隔离。"""
    skill_agent._quota.clear()
    yield
    skill_agent._quota.clear()


@pytest.fixture()
def offline(monkeypatch):
    """GitHub 与联网搜索都不可用（不产生真实网络请求）。"""
    from app.modules.market import skill_agent as mod

    def _no_github(query: str, limit: int = 10) -> list:
        raise AppError("NETWORK_UNAVAILABLE", "当前设备无网络连接。", http_status=503)

    async def _no_web(db, yard_id, query, count=5, lang="zh-CN"):
        raise AppError("SEARCH_PROVIDER_UNAVAILABLE", "当前设备无网络连接。", http_status=503)

    monkeypatch.setattr(mod, "search_github_skills", _no_github)
    monkeypatch.setattr(mod, "search_web", _no_web)


from app.core.errors import AppError  # noqa: E402  （offline fixture 依赖，置后导入）

# ---------------------------------------------------------------------------
# HTTP 端点（kind=plugin）
# ---------------------------------------------------------------------------


def test_generate_plugin_registers_pending_review(client, db_session, offline):
    yard = create_yard(db_session, name="一句话插件小院")
    db_session.commit()
    resp = client.post(
        f"{BASE}/agent/generate",
        json={"utterance": "做一个浇灌决策插件，先查知识库再给建议", "kind": "plugin"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["plugin"]["status"] == "pending_review"
    assert data["plugin"]["name"]
    steps = data["manifest"]["steps"]
    assert steps and all(s["kind"] in ("tool", "llm") for s in steps)
    # 数据库行：小院级、待审、workflow 类型
    row = (
        db_session.query(PluginManifest)
        .filter(PluginManifest.plugin_id == data["plugin"]["plugin_id"])
        .one()
    )
    assert row.yard_id == yard.id
    assert row.status == "pending_review"
    assert row.type == "workflow"
    # 审计
    logs = (
        db_session.query(AuditLog)
        .filter(AuditLog.yard_id == yard.id, AuditLog.action == "plugin_generated")
        .all()
    )
    assert logs, "插件生成必须落审计"


def test_generate_plugin_requires_skills_generate_permission(client, db_session, offline):
    yard = create_yard(db_session, name="一句话插件小院")
    db_session.commit()
    resp = client.post(
        f"{BASE}/agent/generate",
        json={"utterance": "做一个插件", "kind": "plugin"},
        headers=_hdr(yard_id=yard.id, roles=("service_worker",)),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_generate_plugin_respects_skill_lab_disabled(client, db_session, offline):
    yard = create_yard(db_session, name="一句话插件小院")
    db_session.commit()
    settings_service.update_setting(
        db_session,
        yard_id=yard.id,
        key="skill_lab",
        value={"enabled": False},
        actor_id="u-plugin-agent",
        request_id="req",
    )
    db_session.commit()
    resp = client.post(
        f"{BASE}/agent/generate",
        json={"utterance": "做一个插件", "kind": "plugin"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "SKILL_LAB_DISABLED"


def test_plugin_and_skill_share_daily_quota(client, db_session, offline):
    """插件与技能共用 skill_lab.daily_limit：各生成一次后配额用尽。"""
    yard = create_yard(db_session, name="一句话插件小院")
    db_session.commit()
    settings_service.update_setting(
        db_session,
        yard_id=yard.id,
        key="skill_lab",
        value={"daily_limit": 1},
        actor_id="u-plugin-agent",
        request_id="req",
    )
    db_session.commit()
    first = client.post(
        f"{BASE}/agent/generate",
        json={"utterance": "做一个插件甲", "kind": "plugin", "use_web": False},
        headers=_hdr(yard_id=yard.id),
    )
    assert first.status_code == 200, first.text
    second = client.post(
        f"{BASE}/agent/generate",
        json={"utterance": "做一个技能乙", "use_web": False},
        headers=_hdr(yard_id=yard.id),
    )
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "SKILL_QUOTA_EXCEEDED"


def test_generate_plugin_invalid_kind_falls_back_to_skill(client, db_session, offline):
    """kind 缺省/未知值一律按技能处理（向后兼容 v3.2 调用方）。"""
    yard = create_yard(db_session, name="一句话插件小院")
    db_session.commit()
    resp = client.post(
        f"{BASE}/agent/generate",
        json={"utterance": "做一个量子纠缠番茄嫁接技能", "kind": "whatever", "use_web": False},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["skill"]["visibility"] == "private"


# ---------------------------------------------------------------------------
# 对话接线（真实模块，不打桩）
# ---------------------------------------------------------------------------


def test_chat_plugin_turn_emits_plugin_card(client, db_session, offline):
    yard = create_yard(db_session, name="一句话插件小院")
    db_session.commit()
    sid = client.post(CHAT, json={}, headers=_hdr(yard_id=yard.id)).json()["data"]["id"]
    resp = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": "帮我做一个浇灌决策插件，先查知识库再给建议"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    message = resp.json()["data"]["assistant_message"]
    assert message["routed_by"] == "local"
    payload = message["payload"]
    assert payload["type"] == "plugin_card"
    assert payload["state"] == "created"
    assert payload["plugin"]["status"] == "pending_review"
    assert payload["manifest"]["steps"]
    assert message["steps"][-1]["status"] == "done"
    # 落库为待审插件
    row = (
        db_session.query(PluginManifest)
        .filter(PluginManifest.plugin_id == payload["plugin"]["plugin_id"])
        .one()
    )
    assert row.status == "pending_review"


def test_chat_generate_denied_without_permission(client, db_session):
    """自定义角色裁掉 skills:generate：对话里同样生成不了（403，失败行落库）。"""
    yard = create_yard(db_session, name="一句话插件小院")
    db_session.commit()
    denied_hdr = _hdr(yard_id=yard.id, user_id="u-no-gen", roles=("service_worker",))
    sid = client.post(CHAT, json={}, headers=denied_hdr).json()["data"]["id"]
    resp = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": "我需要一个量子纠缠番茄嫁接技能"},
        headers=denied_hdr,
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "FORBIDDEN"
