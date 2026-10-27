"""一句话要技能的接线测试：意图 → ``handle_skill_request`` → 卡片落库回放。

覆盖三件事（v3.2.0）：

1. **三种卡片状态都按同一形状返回**：``content`` 取卡片 ``note``（为空给中文
   摘要）、``choice={"kind":"skill",…}``、``payload`` 原样透传、``routed_by=local``；
2. **卡片随助手消息落库**：``payload_json`` 写进 ``chat_messages``，刷新后
   ``GET /messages`` 仍能读回同一份卡片与步骤；
3. **过程步骤可解释**：``on_step`` 至少收到两条，成功最后一条 ``done``、
   失败最后一条 ``failed`` 且带原因。

``market.skill_agent`` 由并行开发提供，这里用 **桩模块** 注入 ``sys.modules``，
不创建占位文件、不触碰真实 LLM/网络。
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.security import create_access_token
from app.modules.chat import service
from app.modules.yards.repository import create_yard

CHAT = "/api/v1/chat/sessions"
SKILL_MODULE = "app.modules.market.skill_agent"


# ---------------------------------------------------------------------------
# 桩模块：替换并行开发中的 market.skill_agent
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def skill_agent_stub(monkeypatch):
    """在 ``sys.modules`` 里注入桩模块：未显式绑定时任何调用都算测试配错。"""

    async def _unconfigured(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("测试必须先 monkeypatch handle_skill_request")

    module = types.ModuleType(SKILL_MODULE)
    module.handle_skill_request = _unconfigured  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, SKILL_MODULE, module)
    import app.modules.market as market_pkg

    monkeypatch.setattr(market_pkg, "skill_agent", module, raising=False)
    return module


def _bind_card(monkeypatch, module, card: dict[str, Any]) -> list[dict[str, Any]]:
    """把入口换成固定卡片，并记录调用参数（utterance/on_step）。"""

    calls: list[dict[str, Any]] = []

    async def fake(db, *, ctx, utterance: str, on_step=None):
        calls.append({"db": db, "ctx": ctx, "utterance": utterance, "on_step": on_step})
        return card

    monkeypatch.setattr(module, "handle_skill_request", fake)
    return calls


# ---------------------------------------------------------------------------
# 造数据（与 test_chat_mention / test_chat_group_turn 同一套鉴权口径）
# ---------------------------------------------------------------------------


def _hdr(user_id: str = "u-skill", yard_id: str | None = None, roles: list[str] | None = None):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=roles or ["yard_admin"])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def yard(db_session):
    y = create_yard(db_session, name="一句话技能小院")
    db_session.commit()
    return y


def _mk_session(client, yard, user_id: str = "u-skill") -> str:
    resp = client.post(CHAT, json={}, headers=_hdr(user_id, yard.id))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def _send(client, yard, sid: str, content: str) -> dict[str, Any]:
    resp = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": content},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _skill(**over: Any) -> dict[str, Any]:
    base = {
        "id": "sk-1",
        "code": "pest_diagnosis",
        "name": "病虫害防治",
        "visibility": "private",
        "status": "draft",
    }
    base.update(over)
    return base


def _matched_card(utterance: str) -> dict[str, Any]:
    """匹配命中：卡片自带 token_usage，验证原样透传。"""
    return {
        "type": "skill_card",
        "state": "matched",
        "utterance": utterance,
        "skill": _skill(),
        "matches": [
            {
                "item_key": "builtin:pest",
                "name": "病虫害防治",
                "source": "builtin",
                "summary": "识别常见病虫害并给出防治建议",
                "license": "MIT",
                "installed": False,
            }
        ],
        "sources": [{"title": "农技手册", "url": "https://example.com/pest"}],
        "note": "找到 1 个匹配技能：病虫害防治。",
        "token_usage": {
            "input_tokens": 11,
            "output_tokens": 7,
            "total_tokens": 18,
            "model": "test_skill",
        },
    }


def _created_card(utterance: str) -> dict[str, Any]:
    """无匹配自动生成：note 留空，验证中文摘要兜底与估算 token。"""
    return {
        "type": "skill_card",
        "state": "created",
        "utterance": utterance,
        "skill": _skill(id="sk-2", code="soil_quick_test", name="土壤速测"),
        "matches": [],
        "sources": [{"title": "土壤速测规范", "url": "https://example.com/soil"}],
        "note": "",
    }


def _failed_card(utterance: str) -> dict[str, Any]:
    """生成失败：skill 为 null，验证 choice 兜底名与最后一步 failed。"""
    return {
        "type": "skill_card",
        "state": "failed",
        "utterance": utterance,
        "skill": None,
        "matches": [],
        "sources": [],
        "note": "今日技能生成配额已用尽。",
    }


# ---------------------------------------------------------------------------
# execute_turn：on_step 阶段与返回形状
# ---------------------------------------------------------------------------


async def test_created_card_emits_expected_steps(db_session, yard, skill_agent_stub, monkeypatch):
    content = "我需要一个土壤速测技能"
    card = _created_card(content)
    calls = _bind_card(monkeypatch, skill_agent_stub, card)
    ctx = YardContext(yard_id=yard.id, user_id="u-skill", roles=["yard_admin"], request_id="req")

    steps: list[tuple[str, str, str]] = []

    def on_step(phase: str, text: str, status: str) -> None:
        steps.append((phase, text, status))

    out = await service.execute_turn(
        db_session,
        ctx=ctx,
        session_id="sess-skill",
        content=content,
        attachments=[],
        on_step=on_step,
    )

    assert calls and calls[0]["utterance"] == content
    assert calls[0]["on_step"] is on_step  # 入口原样收到回调（内部可继续上报）
    texts = [s[1] for s in steps]
    assert "正在检索已有技能" in texts
    assert "未找到匹配，正在学习并生成技能" in texts
    assert "技能草稿已生成" in texts
    assert len(steps) >= 2 and steps[-1][2] == "done"

    assert out["content"]  # note 为空 → 中文摘要兜底
    assert "技能草稿" in out["content"]
    assert out["routed_by"] == service.ROUTED_LOCAL
    assert out["choice"] == {"kind": "skill", "id": "sk-2", "name": "土壤速测"}
    assert out["payload"] == card
    assert "技能" in out["reason"]
    assert out["token_usage"]["model"] == "skill_generator"
    assert out["token_usage"]["estimated"] is True


async def test_matched_card_emits_done_and_passes_token_usage(
    db_session, yard, skill_agent_stub, monkeypatch
):
    content = "我需要一个病虫害防治技能"
    card = _matched_card(content)
    _bind_card(monkeypatch, skill_agent_stub, card)
    ctx = YardContext(yard_id=yard.id, user_id="u-skill", roles=["yard_admin"], request_id="req")

    steps: list[tuple[str, str, str]] = []
    out = await service.execute_turn(
        db_session,
        ctx=ctx,
        session_id="sess-skill",
        content=content,
        attachments=[],
        on_step=lambda p, t, s: steps.append((p, t, s)),
    )

    assert len(steps) >= 2 and steps[-1][2] == "done"
    assert out["content"] == card["note"]
    assert out["choice"] == {"kind": "skill", "id": "sk-1", "name": "病虫害防治"}
    assert out["token_usage"] == card["token_usage"]


async def test_failed_card_marks_last_step_failed(db_session, yard, skill_agent_stub, monkeypatch):
    content = "帮我做一个病虫害技能"
    card = _failed_card(content)
    _bind_card(monkeypatch, skill_agent_stub, card)
    ctx = YardContext(yard_id=yard.id, user_id="u-skill", roles=["yard_admin"], request_id="req")

    steps: list[tuple[str, str, str]] = []
    out = await service.execute_turn(
        db_session,
        ctx=ctx,
        session_id="sess-skill",
        content=content,
        attachments=[],
        on_step=lambda p, t, s: steps.append((p, t, s)),
    )

    assert len(steps) >= 2
    assert steps[-1][2] == "failed"
    assert card["note"] in steps[-1][1]  # 失败原因写在最后一步
    assert out["content"] == card["note"]
    assert out["choice"] == {"kind": "skill", "id": "", "name": "技能生成"}
    assert out["payload"] == card


async def test_app_error_is_not_swallowed(db_session, yard, skill_agent_stub, monkeypatch):
    """错误继续上抛（与能力链路一致），但最后一步先标 failed。"""

    async def boom(db, *, ctx, utterance: str, on_step=None):
        raise AppError("SKILL_GEN_FAILED", "技能生成服务暂不可用。", http_status=503)

    monkeypatch.setattr(skill_agent_stub, "handle_skill_request", boom)
    ctx = YardContext(yard_id=yard.id, user_id="u-skill", roles=["yard_admin"], request_id="req")
    steps: list[tuple[str, str, str]] = []

    with pytest.raises(AppError) as exc:
        await service.execute_turn(
            db_session,
            ctx=ctx,
            session_id="sess-skill",
            content="我需要一个病虫害技能",
            attachments=[],
            on_step=lambda p, t, s: steps.append((p, t, s)),
        )

    assert exc.value.code == "SKILL_GEN_FAILED"
    assert steps[-1][2] == "failed"


# ---------------------------------------------------------------------------
# HTTP 落库与回放：payload 随助手消息保存，刷新后仍可回看
# ---------------------------------------------------------------------------


def test_matched_card_round_trips_through_history(client, yard, skill_agent_stub, monkeypatch):
    content = "我需要一个病虫害防治技能"
    card = _matched_card(content)
    _bind_card(monkeypatch, skill_agent_stub, card)
    sid = _mk_session(client, yard)

    msg = _send(client, yard, sid, content)["assistant_message"]
    assert msg["role"] == "assistant"
    assert msg["routed_by"] == service.ROUTED_LOCAL
    assert msg["choice"] == {"kind": "skill", "id": "sk-1", "name": "病虫害防治"}
    assert msg["payload"] == card
    assert msg["content"] == card["note"]
    assert msg["token_usage"] == card["token_usage"]
    assert len(msg["steps"]) >= 2 and msg["steps"][-1]["status"] == "done"

    # 刷新后回放：payload/steps 与响应逐字一致（卡片没丢）
    hist = client.get(f"{CHAT}/{sid}/messages", headers=_hdr(yard_id=yard.id)).json()["data"][
        "items"
    ]
    assistant = [m for m in hist if m["role"] == "assistant"]
    assert len(assistant) == 1
    assert assistant[0]["payload"] == card
    assert assistant[0]["steps"] == msg["steps"]


def test_created_card_note_fallback_and_steps_persist(client, yard, skill_agent_stub, monkeypatch):
    content = "生成一个土壤速测 skill"
    card = _created_card(content)
    _bind_card(monkeypatch, skill_agent_stub, card)
    sid = _mk_session(client, yard)

    msg = _send(client, yard, sid, content)["assistant_message"]
    assert msg["content"] == "已生成技能草稿，可在技能库中查看并绑定到助手。"
    assert msg["choice"] == {"kind": "skill", "id": "sk-2", "name": "土壤速测"}
    assert msg["payload"] == card
    assert msg["token_usage"]["model"] == "skill_generator"
    details = " ".join(str(s.get("detail") or "") for s in msg["steps"])
    assert "未找到匹配" in details and "技能草稿已生成" in details
    assert msg["steps"][-1]["status"] == "done"

    hist = client.get(f"{CHAT}/{sid}/messages", headers=_hdr(yard_id=yard.id)).json()["data"][
        "items"
    ]
    assistant = [m for m in hist if m["role"] == "assistant"]
    assert assistant and assistant[0]["payload"] == card


def test_failed_card_persists_failed_step(client, yard, skill_agent_stub, monkeypatch):
    content = "帮我做一个病虫害技能"
    card = _failed_card(content)
    _bind_card(monkeypatch, skill_agent_stub, card)
    sid = _mk_session(client, yard)

    msg = _send(client, yard, sid, content)["assistant_message"]
    assert msg["content"] == card["note"]
    assert msg["choice"] == {"kind": "skill", "id": "", "name": "技能生成"}
    assert msg["payload"] == card
    assert msg["steps"][-1]["status"] == "failed"
    assert card["note"] in msg["steps"][-1]["detail"]

    hist = client.get(f"{CHAT}/{sid}/messages", headers=_hdr(yard_id=yard.id)).json()["data"][
        "items"
    ]
    assistant = [m for m in hist if m["role"] == "assistant"]
    assert assistant and assistant[0]["payload"]["state"] == "failed"
    assert assistant[0]["steps"][-1]["status"] == "failed"
