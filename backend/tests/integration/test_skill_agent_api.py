"""一句话技能链路（v3.2）集成测试：检索、生成、安装、卡片与对话接线。

全部用例都不联网：GitHub 检索与联网搜索都被打桩，模型走 fake provider
（输出非 JSON → 走确定性兜底模板），因此断言是可复现的。
"""

from __future__ import annotations

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.security import create_access_token
from app.modules.audit.models import AuditLog
from app.modules.evolution.models import Skill
from app.modules.market import skill_agent
from app.modules.yards import settings_service
from app.modules.yards.repository import create_yard

BASE = "/api/v1/market"
CHAT = "/api/v1/chat/sessions"
YARD = "yard-skill-agent"
#: 与内置农业技能无共同 2-gram 的utterance（保证检索零命中）
NO_MATCH_UTTERANCE = "做一个量子纠缠番茄嫁接技能"


def _hdr(
    user_id: str = "u-skill-agent",
    yard_id: str = YARD,
    roles: tuple[str, ...] = ("yard_admin",),
) -> dict[str, str]:
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _reset_quota():
    """技能生成配额是进程级状态，用例间必须隔离。"""
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


@pytest.fixture()
def ctx() -> YardContext:
    return YardContext(
        yard_id=YARD, user_id="u-skill-agent", roles=["yard_admin"], request_id="req"
    )


# ---------------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------------


def test_request_matches_builtin_skill(client):
    resp = client.post(
        f"{BASE}/agent/request",
        json={"utterance": "我需要一个土壤健康管理技能"},
        headers=_hdr(),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["recommend"] == "install"
    assert data["matches"], data
    assert data["matches"][0]["item_key"].startswith("builtin:")
    assert data["matches"][0]["source"] == "builtin"
    assert data["matches"][0]["installed"] is False
    # 匹配度归一化到 (0, 1]
    assert 0 < data["matches"][0]["score"] <= 1
    assert data["offline"] is False
    assert "土壤" in data["matches"][0]["name"]


def test_request_without_match_recommends_generate(client, offline):
    resp = client.post(
        f"{BASE}/agent/request",
        json={"utterance": NO_MATCH_UTTERANCE},
        headers=_hdr(),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["recommend"] == "generate"
    assert data["matches"] == []
    assert data["offline"] is True
    assert data["note"]


def test_request_rejects_empty_utterance(client):
    resp = client.post(f"{BASE}/agent/request", json={"utterance": "  "}, headers=_hdr())
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------


def test_generate_creates_private_draft(client, db_session, offline):
    resp = client.post(
        f"{BASE}/agent/generate",
        json={"utterance": "做一个番茄晚疫病识别技能", "use_web": False},
        headers=_hdr(),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    brief = data["skill"]
    assert brief["visibility"] == "private"
    assert brief["status"] == "draft"
    assert brief["version"]
    assert data["token_usage"]["model"]

    skill = db_session.query(Skill).filter(Skill.id == brief["id"]).one()
    assert skill.created_from == "user_generated"
    assert skill.origin == "skill_agent"
    assert skill.owner_user_id == "u-skill-agent"
    assert skill.visibility == "private"
    assert skill.definition_json["prompt"]
    assert skill.definition_json["markdown"].startswith("---")


def test_generate_respects_daily_quota(client, db_session, offline):
    settings_service.update_setting(
        db_session,
        yard_id=YARD,
        key="skill_lab",
        value={"daily_limit": 1},
        actor_id="u-skill-agent",
        request_id="req",
    )
    db_session.commit()
    first = client.post(
        f"{BASE}/agent/generate",
        json={"utterance": "做一个技能甲", "use_web": False},
        headers=_hdr(),
    )
    assert first.status_code == 200, first.text
    second = client.post(
        f"{BASE}/agent/generate",
        json={"utterance": "做一个技能乙", "use_web": False},
        headers=_hdr(),
    )
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "SKILL_QUOTA_EXCEEDED"


def test_generate_rejected_when_skill_lab_disabled(client, db_session, offline):
    settings_service.update_setting(
        db_session,
        yard_id=YARD,
        key="skill_lab",
        value={"enabled": False},
        actor_id="u-skill-agent",
        request_id="req",
    )
    db_session.commit()

    resp = client.post(
        f"{BASE}/agent/generate",
        json={"utterance": "做一个技能", "use_web": False},
        headers=_hdr(),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "SKILL_LAB_DISABLED"

    # 检索链路不受开关影响：仍可命中候选
    matched = client.post(
        f"{BASE}/agent/request",
        json={"utterance": "我需要一个土壤健康管理技能"},
        headers=_hdr(),
    )
    assert matched.status_code == 200, matched.text
    assert matched.json()["data"]["matches"]


def test_generate_requires_skills_generate_permission(client, offline):
    resp = client.post(
        f"{BASE}/agent/generate",
        json={"utterance": "做一个技能", "use_web": False},
        headers=_hdr(roles=("service_worker",)),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_generate_writes_audit_log(client, db_session, offline):
    resp = client.post(
        f"{BASE}/agent/generate",
        json={"utterance": "做一个柑橘保花保果技能", "use_web": False},
        headers=_hdr(),
    )
    assert resp.status_code == 200, resp.text
    skill_id = resp.json()["data"]["skill"]["id"]
    logs = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "skill_generated")
        .order_by(AuditLog.created_at.desc())
        .all()
    )
    assert logs
    assert logs[0].resource_id == skill_id
    assert logs[0].metadata_json["skill_code"]


# ---------------------------------------------------------------------------
# 安装
# ---------------------------------------------------------------------------


def test_install_by_item_key(client):
    key = client.post(f"{BASE}/items/search", json={"query": "轮作"}, headers=_hdr()).json()[
        "data"
    ]["items"][0]["item_key"]
    resp = client.post(f"{BASE}/agent/install", json={"item_key": key}, headers=_hdr())
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["skill_status"] == "published"
    assert data["needs_review"] is False
    assert data["skill_id"]


def test_install_by_skill_id_submits_draft_for_review(client, db_session, offline):
    generated = client.post(
        f"{BASE}/agent/generate",
        json={"utterance": "做一个甜瓜采收分级技能", "use_web": False},
        headers=_hdr(),
    ).json()["data"]
    skill_id = generated["skill"]["id"]

    resp = client.post(f"{BASE}/agent/install", json={"skill_id": skill_id}, headers=_hdr())
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["needs_review"] is True
    assert data["review_status"] == "pending_review"

    skill = db_session.query(Skill).filter(Skill.id == skill_id).one()
    assert skill.status == "pending_review"
    assert skill.visibility == "yard"


def test_install_rejects_missing_payload(client):
    resp = client.post(f"{BASE}/agent/install", json={}, headers=_hdr())
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_REQUEST"


def test_install_unknown_skill_id(client):
    resp = client.post(f"{BASE}/agent/install", json={"skill_id": "sk-none"}, headers=_hdr())
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SKILL_NOT_FOUND"


# ---------------------------------------------------------------------------
# 对话链路入口
# ---------------------------------------------------------------------------


async def test_handle_skill_request_returns_matched_card(db_session, ctx):
    card = await skill_agent.handle_skill_request(
        db_session, ctx=ctx, utterance="我需要一个土壤健康管理技能"
    )
    assert card["type"] == "skill_card"
    assert card["state"] == "matched"
    assert card["skill"] is None
    assert card["matches"]
    assert card["matches"][0]["source"] == "builtin"
    assert card["note"]


async def test_handle_skill_request_generates_draft_when_no_match(db_session, ctx, offline):
    card = await skill_agent.handle_skill_request(db_session, ctx=ctx, utterance=NO_MATCH_UTTERANCE)
    assert card["state"] == "created"
    assert card["skill"]["visibility"] == "private"
    assert card["skill"]["status"] == "draft"
    assert card["token_usage"]["model"]


async def test_handle_skill_request_reports_failed_card_on_quota(db_session, ctx, offline):
    settings_service.update_setting(
        db_session,
        yard_id=YARD,
        key="skill_lab",
        value={"daily_limit": 1},
        actor_id="u-skill-agent",
        request_id="req",
    )
    db_session.commit()
    first = await skill_agent.handle_skill_request(
        db_session, ctx=ctx, utterance=NO_MATCH_UTTERANCE
    )
    assert first["state"] == "created"
    second = await skill_agent.handle_skill_request(
        db_session, ctx=ctx, utterance=NO_MATCH_UTTERANCE
    )
    assert second["state"] == "failed"
    assert second["skill"] is None
    assert "配额" in second["note"]


# ---------------------------------------------------------------------------
# 对话 HTTP 接线（真实模块，不打桩）
# ---------------------------------------------------------------------------


def test_chat_skill_turn_emits_skill_card(client, db_session, offline):
    yard = create_yard(db_session, name="一句话技能小院")
    db_session.commit()
    sid = client.post(CHAT, json={}, headers=_hdr(yard_id=yard.id)).json()["data"]["id"]
    resp = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": "我需要一个土壤健康管理技能"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    message = resp.json()["data"]["assistant_message"]
    assert message["routed_by"] == "local"
    assert message["payload"]["type"] == "skill_card"
    assert message["payload"]["state"] == "matched"
    assert message["steps"][-1]["status"] == "done"
