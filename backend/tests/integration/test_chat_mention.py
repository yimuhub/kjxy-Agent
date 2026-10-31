"""@ 点名的接线测试：``target_code`` 定向执行 + ``/chat/mention-candidates`` + 徽章。

三件事各自最容易坏的地方：
1. **@ 只改"谁来答"，不改"这是不是提问"**：命中 create/install 意图时意图链路
   优先，``target_code`` 绝不把"创建一个助手"劫持成向某助手提问；
2. **候选集只有一份可见性口径**：候选直接复用 ``supervisor.list_candidates`` +
   ``declarative.to_out_list``，别人的私有助手在这里必须也看不到（负向越权）；
3. **徽章由后端单点下发**：``routed_by`` 任何取值都必有非空 ``routed_label``/
   ``routed_tone``（v1.8.0 线上事故=新取值没进前端字典，整条消息渲染崩溃）。

不传 ``target_code`` 的链路必须与历史逐字一致（零回归），故这里只断言新增语义。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from app.core.security import create_access_token
from app.modules.agents import supervisor
from app.modules.agents.supervisor import ROUTED_LABEL_UNKNOWN, ROUTED_TONE_UNKNOWN
from app.modules.chat import group
from app.modules.chat import service as chat_service
from app.modules.chat import stream as chat_stream
from app.modules.chat.models import ChatMessage
from app.modules.chat.service import ROUTED_LOCAL, message_out
from app.modules.yards.repository import create_yard

CHAT = "/api/v1/chat/sessions"
MENTION = "/api/v1/chat/mention-candidates"
AGENTS = "/api/v1/agents/declarative"
TEAMS = "/api/v1/agents/teams"
#: @ 面板候选的字段全集：前端按这份契约渲染，多/少一个字段都是破坏性变更
CANDIDATE_KEYS = {
    "kind",
    "id",
    "code",
    "name",
    "display_name",
    "visibility",
    "emoji",
    "blurb",
}


@pytest.fixture(autouse=True)
def _stream_session_factory(db_session):
    """流式链路自开短连接：注入同一内存库会话工厂（与生产同机制）。"""
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, autocommit=False)
    original = chat_stream._session_factory
    chat_stream._session_factory = factory
    try:
        yield factory
    finally:
        chat_stream._session_factory = original


def _hdr(user_id: str = "u-men", yard_id: str | None = None, roles: list[str] | None = None):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=roles or ["yard_admin"])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def yard(db_session):
    y = create_yard(db_session, name="@ 点名小院")
    db_session.commit()
    return y


def _tid(yard: Any) -> str:
    return yard if isinstance(yard, str) else yard.id


def _mk_session(client, yard, user_id: str = "u-men") -> str:
    resp = client.post(CHAT, json={}, headers=_hdr(user_id, _tid(yard)))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def _mk_bound_session(client, yard, agent_id: str, user_id: str = "u-men") -> str:
    """建一个「与该助手单独对话」的会话：target/target_code 是会话级锁定。"""
    resp = client.post(
        CHAT,
        json={"title": "单独对话", "target": "agent", "target_code": agent_id},
        headers=_hdr(user_id, _tid(yard)),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def _publish(client, yard, code: str, name: str, desc: str, user_id: str = "u-men") -> str:
    created = client.post(
        AGENTS,
        json={
            "agent_code": code,
            "name": name,
            "description": desc,
            "persona_prompt": f"你是{name}。",
            "task_prompt_template": "回答：{{question}}",
            "knowledge_scopes": ["yard"],
            "allowed_tools": [],
        },
        headers=_hdr(user_id, _tid(yard)),
    )
    assert created.status_code == 200, created.text
    aid = created.json()["data"]["id"]
    pub = client.post(f"{AGENTS}/{aid}/publish", headers=_hdr(user_id, _tid(yard)))
    assert pub.status_code == 200, pub.text
    return aid


def _publish_private(client, yard, code: str, name: str, user_id: str) -> str:
    created = client.post(
        AGENTS,
        json={
            "agent_code": code,
            "name": name,
            "description": f"{name}的职责",
            "persona_prompt": f"你是{name}。",
            "task_prompt_template": "回答：{{question}}",
            "knowledge_scopes": ["yard"],
            "allowed_tools": [],
            "visibility": "private",
        },
        headers=_hdr(user_id, _tid(yard)),
    )
    assert created.status_code == 200, created.text
    aid = created.json()["data"]["id"]
    pub = client.post(f"{AGENTS}/{aid}/publish", headers=_hdr(user_id, _tid(yard)))
    assert pub.status_code == 200, pub.text
    return aid


def _send(client, yard, sid: str, content: str, **extra) -> dict[str, Any]:
    resp = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": content, **extra},
        headers=_hdr(yard_id=_tid(yard)),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _sse_frames(text: str) -> list[tuple[str, dict[str, Any]]]:
    frames: list[tuple[str, dict[str, Any]]] = []
    for block in text.split("\n\n"):
        event = ""
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())
        if not event or not data_lines:
            continue
        try:
            frames.append((event, json.loads("\n".join(data_lines))))
        except json.JSONDecodeError:
            frames.append((event, {"_raw": "\n".join(data_lines)}))
    return frames


# ---------------------------------------------------------------------------
# target_code：定向执行（bound）与零回归
# ---------------------------------------------------------------------------


def test_target_code_routes_directed_and_badges_bound(client, yard):
    aid = _publish(client, yard, "men_soil", "土肥师", "负责土壤肥力与施肥方案")
    sid = _mk_session(client, yard)
    data = _send(client, yard, sid, "我的地该施多少肥", target_code=aid)
    msg = data["assistant_message"]
    assert msg["routed_by"] == "bound"
    assert msg["choice"]["id"] == aid
    # 徽章由后端下发：文案与色调都取自 supervisor 那一份字典
    assert msg["routed_label"] == supervisor.routed_label("bound") == "定向执行"
    assert msg["routed_tone"] == supervisor.routed_tone("bound") == "info"


def test_target_code_accepts_agent_code(client, yard):
    """@ 传 code 与传 id 同效（解析顺序 id→code→名称，同 ask_directed）。"""
    _publish(client, yard, "men_pest", "病虫管家", "负责病虫害识别与防治")
    sid = _mk_session(client, yard)
    data = _send(client, yard, sid, "叶片背面有白粉怎么办", target_code="men_pest")
    assert data["assistant_message"]["routed_by"] == "bound"
    assert data["assistant_message"]["choice"]["name"] == "病虫管家"


def test_without_target_code_keeps_three_level_routing(client, yard):
    """不传 @ 即既有链路：响应形状与 routed_by 取值域都不变（零回归）。"""
    _publish(client, yard, "men_irrig", "灌溉顾问", "负责灌溉浇水与水肥安排")
    sid = _mk_session(client, yard)
    data = _send(client, yard, sid, "今天地里需要浇水吗")
    assert set(data) == {"session", "user_message", "assistant_message"}
    msg = data["assistant_message"]
    assert msg["routed_by"] in {"rules", "llm", "fallback"}
    assert msg["routed_label"]  # 历史三级路由取值同样有徽章
    assert msg["routed_tone"]


def test_unknown_target_code_is_404_and_leaves_failed_row(client, yard):
    sid = _mk_session(client, yard)
    resp = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": "随便问一句", "target_code": "no_such_agent"},
        headers=_hdr(yard_id=_tid(yard)),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
    # 失败也要诚实留痕：assistant 行 failed + error_code，绝不静默吞掉
    rows = client.get(f"{CHAT}/{sid}/messages", headers=_hdr(yard_id=_tid(yard))).json()["data"][
        "items"
    ]
    assistant = [m for m in rows if m["role"] == "assistant"]
    assert assistant and assistant[-1]["status"] == "failed"
    assert assistant[-1]["error_code"] == "NOT_FOUND"


def test_create_intent_wins_over_mention(client, yard):
    """ "创建一个助手" + @ 某助手 → 仍走能力链路（local 卡片），不被点名劫持。"""
    aid = _publish(client, yard, "men_qa", "问答助手", "负责日常问答")
    sid = _mk_session(client, yard)
    data = _send(client, yard, sid, "创建一个「农机维修问答」助手", target_code=aid)
    msg = data["assistant_message"]
    assert msg["routed_by"] == ROUTED_LOCAL
    assert msg["payload"]["type"] == "capability"  # 卡片照旧下发
    assert msg["routed_label"] == supervisor.routed_label(ROUTED_LOCAL)


def test_agent_self_inquiry_never_creates_capability(client, yard):
    """ "「某助手」能帮我做什么？" 是咨询，不是建能力——曾因"助手+帮我"误建同名智能体。"""
    _publish(client, yard, "men_self", "自述助手", "负责介绍自己的能力")
    sid = _mk_session(client, yard)
    data = _send(client, yard, sid, "「自述助手」能帮我做什么？")
    msg = data["assistant_message"]
    assert msg["routed_by"] != ROUTED_LOCAL
    assert msg["payload"] is None


def test_install_intent_wins_over_mention(client, yard):
    aid = _publish(client, yard, "men_qa2", "问答助手二号", "负责日常问答")
    sid = _mk_session(client, yard)
    data = _send(client, yard, sid, "安装水稻病虫害识别助手", target_code=aid)
    msg = data["assistant_message"]
    assert msg["routed_by"] == ROUTED_LOCAL
    # 装载意图走能力市场（v1.8 既定链路）：卡片 type=market，不被 @ 劫持成提问
    assert msg["payload"]["type"] == "market"
    assert msg["routed_label"] == supervisor.routed_label(ROUTED_LOCAL)


def test_capability_duplicate_name_auto_suffixes(client, yard, monkeypatch):
    """对话内生成撞名：自动加「-2」后缀落草稿（D3 口径），卡片注明调整。

    v2.0 回归点：名称唯一上线后，重复 e2e 运行残留的同名助手曾让能力生成
    硬报 DUPLICATE_NAME；对话链路必须降级成自动改名而不是把错误抛给用户。
    """
    from app.modules.chat import capability

    async def _fixed_spec(db, ctx, request_text):
        return {
            "name": "农机维修问答",
            "description": "负责农机维修问答",
            "persona_prompt": "你是农机维修问答助手。",
            "task_prompt_template": "回答：{{question}}",
            "emoji": "",
        }

    monkeypatch.setattr(capability, "_spec_from_llm", _fixed_spec)
    _publish(client, yard, "men_dup", "农机维修问答", "先占这个名字")
    sid = _mk_session(client, yard)
    data = _send(client, yard, sid, "创建一个「农机维修问答」助手")
    msg = data["assistant_message"]
    assert msg["routed_by"] == ROUTED_LOCAL
    card = msg["payload"]
    assert card["type"] == "capability"
    assert card["state"] == "created"
    assert card["name"] == "农机维修问答-2"
    assert "自动调整为「农机维修问答-2」" in card["note"]
    # 落库的是 -2 这一行：原名仍归先注册的助手
    agents = client.get(AGENTS, headers=_hdr(yard_id=_tid(yard))).json()["data"]["agents"]
    assert {i["name"] for i in agents} == {"农机维修问答", "农机维修问答-2"}


def test_stream_with_target_code_emits_bound_badge(client, yard):
    aid = _publish(client, yard, "men_market", "行情助手", "负责价格批发行情")
    sid = _mk_session(client, yard)
    resp = client.post(
        f"{CHAT}/{sid}/messages/stream",
        json={"content": "今年白菜什么价", "target_code": aid},
        headers=_hdr(yard_id=_tid(yard)),
    )
    assert resp.status_code == 200, resp.text
    frames = _sse_frames(resp.text)
    assert frames[0][0] == "chat-init"
    assert frames[-1][0] == "chat-end"
    message = [payload for kind, payload in frames if kind == "message"]
    assert message, frames
    assert message[-1]["role"] == "assistant"
    assert message[-1]["routed_by"] == "bound"
    assert message[-1]["routed_label"] == "定向执行"
    assert message[-1]["routed_tone"] == "info"


# ---------------------------------------------------------------------------
# mention-candidates：可见性只有一份口径
# ---------------------------------------------------------------------------


def test_mention_candidates_shape(client, yard):
    aid = _publish(client, yard, "men_cand", "候选助手", "负责给出候选口径说明")
    resp = client.get(MENTION, headers=_hdr(yard_id=_tid(yard)))
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    got = items[0]
    assert set(got) == CANDIDATE_KEYS
    assert got["kind"] == "agent"
    assert got["id"] == aid
    assert got["code"] == "men_cand"
    assert got["name"] == "候选助手"
    assert got["display_name"] == "候选助手"
    assert got["visibility"] == "public"
    assert got["blurb"] == "负责给出候选口径说明"


def test_mention_candidates_include_teams_with_null_visibility(client, yard):
    a1 = _publish(client, yard, "men_t1", "班组甲", "负责甲环节")
    a2 = _publish(client, yard, "men_t2", "班组乙", "负责乙环节")
    team = client.post(
        TEAMS,
        json={
            "name": "田间班组",
            "members": [
                {"agent_id": a1, "role_name": "甲"},
                {"agent_id": a2, "role_name": "乙"},
            ],
        },
        headers=_hdr(yard_id=_tid(yard)),
    )
    assert team.status_code == 200, team.text
    items = client.get(MENTION, headers=_hdr(yard_id=_tid(yard))).json()["data"]["items"]
    teams = [i for i in items if i["kind"] == "team"]
    assert len(teams) == 1
    assert teams[0]["id"] == team.json()["data"]["id"]
    # 班组无可见性概念（全院可用）→ 统一 null，前端按 kind 分支
    assert teams[0]["visibility"] is None
    assert teams[0]["code"] is None


def test_mention_candidates_hide_others_private_agents(client, yard):
    """负向越权：别人的私有助手既不进候选、也不可被 @ 定向执行。"""
    mine = _publish_private(client, yard, "men_mine", "我的私有", user_id="u-men")
    theirs = _publish_private(client, yard, "men_theirs", "他人私有", user_id="u-other")
    items = client.get(MENTION, headers=_hdr(yard_id=_tid(yard))).json()["data"]["items"]
    ids = {i["id"] for i in items}
    assert mine in ids
    assert theirs not in ids
    # 自己的私有助手：展示名带「（…私有）」后缀，name 仍是干净原名（唯一约束不被污染）
    mine_row = next(i for i in items if i["id"] == mine)
    assert mine_row["name"] == "我的私有"
    assert mine_row["display_name"].endswith("私有）")
    assert mine_row["visibility"] == "private"

    sid = _mk_session(client, yard)
    resp = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": "这句话不该被他人私有助手接走", "target_code": theirs},
        headers=_hdr(yard_id=_tid(yard)),
    )
    assert resp.status_code == 404  # 不可见即不存在，不区分 403
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_mention_candidates_are_yard_scoped(client, yard, db_session):
    _publish(client, yard, "men_here", "本院助手", "负责本院事务")
    other = create_yard(db_session, name="邻院")
    db_session.commit()
    _publish(client, other, "men_there", "邻院助手", "负责邻院事务")
    items = client.get(MENTION, headers=_hdr(user_id="u-men2", yard_id=_tid(other))).json()["data"][
        "items"
    ]
    assert {i["code"] for i in items} == {"men_there"}


def test_mention_candidates_need_yard_context(client):
    resp = client.get(MENTION, headers=_hdr())
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "YARD_REQUIRED"


# ---------------------------------------------------------------------------
# 徽章字典：每个 routed_by 取值都必须有非空文案与色调
# ---------------------------------------------------------------------------


def test_badge_nonempty_for_every_routed_value():
    """词表覆盖：三级路由 + bound + local + group（群聊说明行）+ error。

    这是 v1.8.0 事故的根治点：新取值忘了登记就整条消息渲染崩溃，现在兜底
    给确定文案而不是 None——前端拿到的一定是可渲染字符串。
    """
    values = ["rules", "llm", "fallback", "bound", ROUTED_LOCAL, group.ROUTED_GROUP, "error"]
    for value in values:
        assert supervisor.routed_label(value), value
        assert supervisor.routed_tone(value), value
    # 未登记取值 → 兜底文案（非空、非 None）
    assert supervisor.routed_label("brand_new") == ROUTED_LABEL_UNKNOWN
    assert supervisor.routed_tone("brand_new") == ROUTED_TONE_UNKNOWN
    # 无来源（user 行）→ 空串 = 不渲染徽章
    assert supervisor.routed_label(None) == ""
    assert supervisor.routed_tone(None) == ""
    # 消息出参自带徽章：历史行 / 失败行都不漏字段
    out = message_out(ChatMessage(role="assistant", status="ok", routed_by="rules"))
    assert out["routed_label"] == supervisor.routed_label("rules")
    assert out["routed_tone"] == supervisor.routed_tone("rules")
    assert out["routed_by"] == "rules"


def test_group_notice_row_carries_group_badge():
    """群聊说明行（未回答）也走同一份字典：routed_by='group' → 文案非空。"""
    assert (
        message_out(ChatMessage(role="assistant", routed_by=group.ROUTED_GROUP))["routed_label"]
        == "群聊说明"
    )


def test_failed_row_still_badgeable():
    """失败行（routed_by 为空）：文案空串而非 None——前端不会拿到 null。"""
    out = message_out(ChatMessage(role="assistant", status="failed", error_code="NOT_FOUND"))
    assert out["routed_label"] == ""
    assert out["routed_tone"] == ""


# ---------------------------------------------------------------------------
# 会话级锁定：与某个助手单独对话时，整轮只由它回答
# ---------------------------------------------------------------------------


def test_bound_session_stays_on_bound_agent(client, yard):
    """会话 target=agent + target_code → 普通问答不再进入总控自动路由。"""
    soil = _publish(client, yard, "bound_soil", "绑定土肥师", "负责土壤肥力与施肥方案")
    _publish(client, yard, "bound_pest", "绑定病虫管家", "负责病虫害识别与防治")
    sid = _mk_bound_session(client, yard, soil)
    data = _send(client, yard, sid, "叶片背面有白粉怎么办")
    msg = data["assistant_message"]
    assert msg["routed_by"] == "bound"
    assert msg["choice"]["id"] == soil


def test_bound_session_ignores_message_level_override(client, yard):
    """锁定会话里即使单条消息带了别的 target_code，也不能改道。"""
    soil = _publish(client, yard, "bound_soil2", "绑定土肥师二号", "负责土壤肥力与施肥方案")
    pest = _publish(client, yard, "bound_pest2", "绑定病虫管家二号", "负责病虫害识别与防治")
    sid = _mk_bound_session(client, yard, soil)
    data = _send(client, yard, sid, "帮我看看虫害", target_code=pest)
    msg = data["assistant_message"]
    assert msg["routed_by"] == "bound"
    assert msg["choice"]["id"] == soil


def test_bound_session_skips_global_intent_chains(client, yard):
    """锁定会话不走方案/创建助手等全局链路：整轮仍由绑定助手回答。"""
    aid = _publish(client, yard, "bound_qa", "绑定问答助手", "负责日常问答")
    sid = _mk_bound_session(client, yard, aid)

    plan = _send(client, yard, sid, "写一份小麦赤霉病的防治方案")
    pmsg = plan["assistant_message"]
    assert pmsg["routed_by"] == "bound"
    assert pmsg["choice"]["id"] == aid
    assert pmsg["payload"] is None  # 不再产出方案卡

    create = _send(client, yard, sid, "创建一个「农机维修问答」助手")
    cmsg = create["assistant_message"]
    assert cmsg["routed_by"] == "bound"
    assert cmsg["choice"]["id"] == aid
    assert cmsg["payload"] is None  # 不再产出能力草稿卡


def test_bound_session_carries_previous_turns(client, yard, monkeypatch):
    """锁定会话把本会话此前几轮带给助手，避免每轮都像第一次见面。"""
    aid = _publish(client, yard, "bound_ctx", "绑定上下文档手", "负责连续对话")
    sid = _mk_bound_session(client, yard, aid)
    seen: list[str] = []

    async def fake_directed(db, *, ctx, question, target, prefer_team=False):
        seen.append(question)
        return {
            "choice": {"kind": "agent", "id": aid, "code": "bound_ctx", "name": "绑定上下文档手"},
            "routed_by": "bound",
            "reason": "",
            "result": {
                "answer": f"第 {len(seen)} 轮回答",
                "token_usage": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "total_tokens": 2,
                },
            },
        }

    monkeypatch.setattr(chat_service.supervisor, "ask_directed", fake_directed)
    _send(client, yard, sid, "第一句话")
    _send(client, yard, sid, "第二句话")
    assert "【本会话此前的对话" not in seen[0]
    assert "【本会话此前的对话" in seen[1]
    assert "第一句话" in seen[1]
    assert "第 1 轮回答" in seen[1]
    # 当前轮不重复塞进历史
    assert seen[1].count("第二句话") == 1
