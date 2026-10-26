"""多助手群聊编排单测：策略选人、auto 门槛、扇出并发与部分失败。

模型调用全部打桩（``supervisor.ask_directed``）：这里验的是**编排**——
每位应答者一条消息、成员事件数、并发上限、预算封顶、挂掉的成员如何诚实呈现。
真实链路（附件/知识检索/fake provider）在 tests/integration/test_yard_group_chat_api.py。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.progress import PHASE_ROUTING
from app.modules.agents.models import STATUS_PUBLISHED, YardAgent
from app.modules.chat import group, service
from app.modules.chat.models import (
    MSG_FAILED,
    POLICY_ALL,
    POLICY_AUTO,
    POLICY_NAMED,
    ROLE_ASSISTANT,
    ChatMessage,
    ChatParticipant,
)
from app.modules.yards.repository import create_yard
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

# ---------------------------------------------------------------------------
# 夹具与打桩
# ---------------------------------------------------------------------------


@pytest.fixture()
def factory(db_session):
    """成员任务自开短连接的注入点（与生产/流式测试同一机制）。"""
    original = group._session_factory
    group._session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    try:
        yield group._session_factory
    finally:
        group._session_factory = original


@pytest.fixture()
def yard(db_session):
    y = create_yard(db_session, name="群聊单测小院")
    db_session.commit()
    return y


def _ctx(yard_id: str) -> YardContext:
    return YardContext(yard_id=yard_id, user_id="u-owner", roles=["yard_admin"], request_id="req_t")


def _agent(db_session, yard_id: str, code: str, name: str, desc: str) -> str:
    a = YardAgent(
        yard_id=yard_id,
        agent_code=code,
        name=name,
        description=desc,
        persona_prompt=f"你是{name}。",
        task_prompt_template="回答：{{question}}",
        allowed_tools=[],
        knowledge_scopes=["yard"],
        status=STATUS_PUBLISHED,
    )
    db_session.add(a)
    db_session.commit()
    return a.id


def _participants(*specs: tuple[str, str]) -> list[dict[str, Any]]:
    """构造名单纯数据（position 从 1 递增，与库读出的形状一致）。"""
    return [
        {
            "id": f"p{i}",
            "session_id": "s1",
            "agent_id": aid,
            "agent_code": f"code{i}",
            "agent_name": name,
            "position": i,
        }
        for i, (aid, name) in enumerate(specs, start=1)
    ]


class _Stub:
    """ask_directed 打桩：记录并发在飞数、按 target 决定回答/抛错/空答。"""

    def __init__(self) -> None:
        self.inflight = 0
        self.max_inflight = 0
        self.calls: list[str] = []
        self.failing: set[str] = set()
        self.empty: set[str] = set()
        self.sleep = 0.01

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake(db, *, ctx, question, target, prefer_team=False):  # noqa: ANN001
            self.calls.append(str(target))
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
            try:
                await asyncio.sleep(self.sleep)
                if str(target) in self.failing:
                    raise AppError("LLM_ERROR", "供应商连接失败。", http_status=502)
                answer = "" if str(target) in self.empty else f"{target} 的回答：{question[:12]}"
                return {
                    "choice": {"kind": "agent", "id": str(target), "code": "", "name": str(target)},
                    "routed_by": "bound",
                    "reason": "定向执行",
                    "result": {"kind": "agent", "answer": answer},
                }
            finally:
                self.inflight -= 1

        monkeypatch.setattr(group.supervisor, "ask_directed", fake)


# ---------------------------------------------------------------------------
# auto 门槛（纯函数，可解释）
# ---------------------------------------------------------------------------


def test_keyword_confidence_counts_distinct_hits():
    text = "灌溉顾问 负责灌溉浇水与水肥安排"
    assert group.keyword_confidence("今天地里需要浇水吗", text) == pytest.approx(0.6)
    assert group.keyword_confidence("灌溉和浇水都要安排", text) == pytest.approx(0.8)
    assert group.keyword_confidence("政策补贴怎么申报", text) == 0.0


def test_gate_declines_and_explains_when_profile_irrelevant():
    d = group.gate_decision(
        "政策补贴怎么申报", agent_name="灌溉顾问", blurb="负责灌溉浇水", llm=None
    )
    assert d["speak"] is False
    assert "简介与问题不相关" in d["reason"]
    assert f"门槛 {group.ANSWER_THRESHOLD}" in d["reason"]


def test_gate_volunteers_on_keyword_alone_without_llm():
    """模型自判不可用时走关键词兜底，理由里必须写明是兜底（不伪装智能）。"""
    d = group.gate_decision(
        "今天地里需要浇水吗", agent_name="灌溉顾问", blurb="负责灌溉浇水与水肥安排", llm=None
    )
    assert d["speak"] is True
    assert d["basis"] == "简介关键词兜底"


def test_gate_llm_volunteer_beats_no_keyword_match():
    d = group.gate_decision(
        "这套方案你怎么看",
        agent_name="数据分析师",
        blurb="整理上报数据",
        llm={"willing": True, "confidence": 0.9, "reason": "数据类问题我熟"},
    )
    assert d["speak"] is True
    assert d["basis"] == "助手自判"
    assert d["confidence"] == pytest.approx(0.63)


def test_gate_confident_refusal_scores_low():
    d = group.gate_decision(
        "水稻病害识别一下",
        agent_name="灌溉顾问",
        blurb="负责灌溉浇水",
        llm={"willing": False, "confidence": 0.95, "reason": "病害不归我管"},
    )
    assert d["speak"] is False
    assert "病害不归我管" in d["reason"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            '{"willing": true, "confidence": 0.8, "reason":"行"}',
            {"willing": True, "confidence": 0.8},
        ),
        ('{"willing": "是", "confidence": "0.4"}', {"willing": True, "confidence": 0.4}),
        ('{"confidence": 0.4}', None),
        ("不是 JSON", None),
        ('{"willing": true, "confidence": 9}', {"willing": True, "confidence": 1.0}),
    ],
)
def test_parse_gate_response(raw: str, expected: dict | None):
    got = group.parse_gate_response(raw)
    if expected is None:
        assert got is None
    else:
        assert got["willing"] is expected["willing"]
        assert got["confidence"] == pytest.approx(expected["confidence"])


# ---------------------------------------------------------------------------
# 策略选人
# ---------------------------------------------------------------------------


def test_pick_members_all_returns_every_participant_in_order():
    ps = _participants(("a1", "甲"), ("a2", "乙"), ("a3", "丙"))
    assert [p["agent_id"] for p in group.pick_members(POLICY_ALL, ps, None)] == ["a1", "a2", "a3"]


def test_pick_members_named_matches_id_code_or_name():
    ps = _participants(("a1", "甲"), ("a2", "乙"))
    assert group.pick_members(POLICY_NAMED, ps, "a2")[0]["agent_name"] == "乙"
    assert group.pick_members(POLICY_NAMED, ps, "code1")[0]["agent_id"] == "a1"
    assert group.pick_members(POLICY_NAMED, ps, "甲")[0]["agent_id"] == "a1"


def test_pick_members_named_without_target_is_400():
    ps = _participants(("a1", "甲"))
    with pytest.raises(AppError) as e:
        group.pick_members(POLICY_NAMED, ps, None)
    assert e.value.code == "GROUP_TARGET_REQUIRED" and e.value.http_status == 400


def test_pick_members_named_outsider_is_404():
    ps = _participants(("a1", "甲"))
    with pytest.raises(AppError) as e:
        group.pick_members(POLICY_NAMED, ps, "别人")
    assert e.value.code == "GROUP_TARGET_NOT_PARTICIPANT" and e.value.http_status == 404


def test_pick_members_without_roster_is_400():
    with pytest.raises(AppError) as e:
        group.pick_members(POLICY_ALL, [], None)
    assert e.value.code == "GROUP_NO_PARTICIPANTS" and e.value.http_status == 400


# ---------------------------------------------------------------------------
# 一轮群聊的执行（模型调用打桩）
# ---------------------------------------------------------------------------


async def test_policy_all_persists_one_message_per_member(db_session, factory, yard, monkeypatch):
    stub = _Stub()
    stub.install(monkeypatch)
    a1 = _agent(db_session, yard.id, "irrig", "灌溉顾问", "负责灌溉浇水")
    a2 = _agent(db_session, yard.id, "pest", "植保顾问", "负责病虫害防治")
    sess = service.create_session(db_session, yard_id=yard.id, created_by="u-owner")
    db_session.commit()
    # 与真实链路同序：用户发言先落库（seq=1），群聊回答在其后续号
    service.persist_user_message(
        db_session, ctx=_ctx(yard.id), session=sess, content="今天地里需要浇水吗", attachments=[]
    )
    db_session.commit()
    events: list[tuple[str, dict[str, Any]]] = []

    outcome = await group.run_group_turn(
        db_session,
        ctx=_ctx(yard.id),
        session_id=sess.id,
        content="今天地里需要浇水吗",
        attachment_ids=[],
        policy=POLICY_ALL,
        participants=_participants((a1, "灌溉顾问"), (a2, "植保顾问")),
        on_event=lambda kind, payload: events.append((kind, payload)),
    )

    assert outcome["member_count"] == 2
    assert len(outcome["messages"]) == 2 and len(outcome["not_answered"]) == 0
    ok_events = [p for k, p in events if k == "member_event" and p["status"] == group.MEMBER_OK]
    assert len(ok_events) == 2  # 每位应答者一条成员事件
    assert [p["member_index"] for p in ok_events] == [1, 2]
    # 每人一条 assistant 消息，各自带自己的来源存证（choice_json）
    rows = db_session.scalars(
        select(ChatMessage).where(
            ChatMessage.session_id == sess.id,
            ChatMessage.role == ROLE_ASSISTANT,
        )
    )
    msgs = list(rows)
    assert len(msgs) == 2
    assert {m.choice_json["id"] for m in msgs} == {a1, a2}
    assert all(m.routed_by == "bound" for m in msgs)
    assert [m.seq for m in msgs] == [2, 3]  # 用户发言 1，回答按成员顺序续号
    assert outcome["notice"] is None


async def test_policy_named_produces_exactly_one_message(db_session, factory, yard, monkeypatch):
    stub = _Stub()
    stub.install(monkeypatch)
    a1 = _agent(db_session, yard.id, "irrig", "灌溉顾问", "负责灌溉浇水")
    a2 = _agent(db_session, yard.id, "pest", "植保顾问", "负责病虫害防治")
    sess = service.create_session(db_session, yard_id=yard.id, created_by="u-owner")
    db_session.commit()
    events: list[tuple[str, dict[str, Any]]] = []

    outcome = await group.run_group_turn(
        db_session,
        ctx=_ctx(yard.id),
        session_id=sess.id,
        content="看看虫子",
        attachment_ids=[],
        policy=POLICY_NAMED,
        target=a2,
        participants=_participants((a1, "灌溉顾问"), (a2, "植保顾问")),
        on_event=lambda kind, payload: events.append((kind, payload)),
    )

    assert [m["choice"]["id"] for m in outcome["messages"]] == [a2]
    assert stub.calls == [a2]  # 只打被点名那位（省掉另一次模型调用）
    assert len(outcome["messages"]) == 1 and outcome["notice"] is None


async def test_policy_auto_records_decline_reason(db_session, factory, yard, monkeypatch):
    """auto：简介对口的答，判退的必须留下"为什么没答"的步骤，不许静默。"""
    stub = _Stub()
    stub.install(monkeypatch)
    a1 = _agent(db_session, yard.id, "irrig", "灌溉顾问", "负责灌溉浇水与水肥")
    a2 = _agent(db_session, yard.id, "market", "行情助手", "负责价格批发行情")
    sess = service.create_session(db_session, yard_id=yard.id, created_by="u-owner")
    db_session.commit()

    outcome = await group.run_group_turn(
        db_session,
        ctx=_ctx(yard.id),
        session_id=sess.id,
        content="今天地里需要浇水吗",
        attachment_ids=[],
        policy=POLICY_AUTO,
        participants=_participants((a1, "灌溉顾问"), (a2, "行情助手")),
    )

    assert len(outcome["messages"]) >= 1
    assert outcome["answered_names"] == ["灌溉顾问"]
    assert [x["name"] for x in outcome["not_answered"]] == ["行情助手"]
    assert outcome["not_answered"][0]["status"] == "declined"
    assert "简介与问题不相关" in outcome["not_answered"][0]["reason"]
    # 判退理由进了全局步骤条（前端 step 帧 + 说明消息都看得到）
    decline_steps = [
        s for s in outcome["steps"] if s["phase"] == PHASE_ROUTING and "行情助手" in s["detail"]
    ]
    assert decline_steps, "判退必须写进步骤，否则用户只看到沉默"
    # 说明消息落库：谁没答、为什么，历史回放可见
    notice = outcome["notice"]
    assert notice and "「行情助手」未回答" in notice["content"]
    assert notice["routed_by"] == group.ROUTED_GROUP
    assert notice["choice"]["not_answered"][0]["name"] == "行情助手"
    assert notice["steps"]


async def test_one_member_provider_failure_still_streams_others(
    db_session, factory, yard, monkeypatch
):
    stub = _Stub()
    stub.install(monkeypatch)
    a1 = _agent(db_session, yard.id, "irrig", "灌溉顾问", "负责灌溉浇水")
    a2 = _agent(db_session, yard.id, "pest", "植保顾问", "负责病虫害防治")
    sess = service.create_session(db_session, yard_id=yard.id, created_by="u-owner")
    db_session.commit()
    stub.failing = {a2}
    events: list[tuple[str, dict[str, Any]]] = []

    outcome = await group.run_group_turn(
        db_session,
        ctx=_ctx(yard.id),
        session_id=sess.id,
        content="浇水分行吗",
        attachment_ids=[],
        policy=POLICY_ALL,
        participants=_participants((a1, "灌溉顾问"), (a2, "植保顾问")),
        on_event=lambda kind, payload: events.append((kind, payload)),
    )

    assert [m["choice"]["id"] for m in outcome["messages"]] == [a1]  # 好的那位照常回答
    err_events = [p for k, p in events if k == "member_event" and p["status"] == group.MEMBER_ERROR]
    assert len(err_events) == 1 and err_events[0]["error_text"] == "供应商连接失败。"
    assert outcome["not_answered"][0]["error_code"] == "LLM_ERROR"
    assert "「植保顾问」未回答：供应商连接失败。" in outcome["notice"]["content"]
    # 有人答了 → 说明行 status=ok（整轮没人答才 failed，见下一条用例）
    assert outcome["notice"]["status"] == "ok"
    # 消息帧仍然下发：好的那位没被挂掉的同伴带走
    assert [p["id"] for k, p in events if k == "message"][0] == outcome["messages"][0]["id"]


async def test_no_member_answers_marks_notice_failed(db_session, factory, yard, monkeypatch):
    stub = _Stub()
    stub.install(monkeypatch)
    a1 = _agent(db_session, yard.id, "irrig", "灌溉顾问", "负责灌溉浇水")
    sess = service.create_session(db_session, yard_id=yard.id, created_by="u-owner")
    db_session.commit()
    stub.empty = {a1}

    outcome = await group.run_group_turn(
        db_session,
        ctx=_ctx(yard.id),
        session_id=sess.id,
        content="浇水分行吗",
        attachment_ids=[],
        policy=POLICY_ALL,
        participants=_participants((a1, "灌溉顾问")),
    )
    assert outcome["messages"] == []
    assert outcome["notice"]["status"] == MSG_FAILED
    assert outcome["notice"]["error_code"] == "GROUP_NO_ANSWER"


async def test_concurrency_cap_holds_with_more_members_than_slots(
    db_session, factory, yard, monkeypatch
):
    stub = _Stub()
    stub.install(monkeypatch)
    stub.sleep = 0.05
    monkeypatch.setattr(group, "GROUP_CONCURRENCY", 2)
    sess = service.create_session(db_session, yard_id=yard.id, created_by="u-owner")
    ps = []
    for i in range(5):
        aid = _agent(db_session, yard.id, f"c{i}", f"助手{i}", "通用问答")
        ps.append((aid, f"助手{i}"))
    db_session.commit()

    await group.run_group_turn(
        db_session,
        ctx=_ctx(yard.id),
        session_id=sess.id,
        content="随便问问",
        attachment_ids=[],
        policy=POLICY_ALL,
        participants=_participants(*ps),
    )
    assert stub.max_inflight <= 2
    assert len(stub.calls) == 5


async def test_per_turn_answer_budget_caps_model_calls(db_session, factory, yard, monkeypatch):
    stub = _Stub()
    stub.install(monkeypatch)
    monkeypatch.setattr(group, "MAX_ANSWERS_PER_TURN", 2)
    sess = service.create_session(db_session, yard_id=yard.id, created_by="u-owner")
    ps = []
    for i in range(4):
        aid = _agent(db_session, yard.id, f"b{i}", f"预算助手{i}", "通用问答")
        ps.append((aid, f"预算助手{i}"))
    db_session.commit()

    outcome = await group.run_group_turn(
        db_session,
        ctx=_ctx(yard.id),
        session_id=sess.id,
        content="随便问问",
        attachment_ids=[],
        policy=POLICY_ALL,
        participants=_participants(*ps),
    )
    assert len(stub.calls) == 2  # 预算外的成员根本不打模型
    assert len(outcome["messages"]) == 2
    assert len(outcome["not_answered"]) == 2
    assert "本轮应答预算" in outcome["not_answered"][0]["reason"]


# ---------------------------------------------------------------------------
# 名单校验（小院边界）
# ---------------------------------------------------------------------------


def test_replace_participants_rejects_foreign_yard_agent(db_session, yard):
    other = create_yard(db_session, name="隔壁小院")
    db_session.commit()
    foreign = _agent(db_session, other.id, "x1", "隔壁助手", "通用问答")
    sess = service.create_session(db_session, yard_id=yard.id, created_by="u-owner")
    db_session.commit()

    with pytest.raises(AppError) as e:
        group.replace_participants(
            db_session, ctx=_ctx(yard.id), session_id=sess.id, agent_ids=[foreign]
        )
    assert e.value.code == "GROUP_PARTICIPANT_INVALID"
    assert (
        db_session.scalars(
            select(ChatParticipant).where(ChatParticipant.session_id == sess.id)
        ).all()
        == []
    )


def test_replace_participants_overwrites_in_order_and_snapshots_name(db_session, yard):
    a1 = _agent(db_session, yard.id, "irrig", "灌溉顾问", "负责灌溉浇水")
    a2 = _agent(db_session, yard.id, "pest", "植保顾问", "负责病虫害防治")
    sess = service.create_session(db_session, yard_id=yard.id, created_by="u-owner")
    db_session.commit()

    items = group.replace_participants(
        db_session, ctx=_ctx(yard.id), session_id=sess.id, agent_ids=[a2, a1, a1]
    )
    db_session.commit()
    assert [p["agent_name"] for p in items] == ["植保顾问", "灌溉顾问"]
    assert [p["position"] for p in items] == [1, 2]

    items2 = group.replace_participants(
        db_session, ctx=_ctx(yard.id), session_id=sess.id, agent_ids=[a1]
    )
    db_session.commit()
    assert [p["agent_id"] for p in items2] == [a1]
    assert (
        group.list_participants(
            db_session, yard_id=yard.id, created_by="u-owner", session_id=sess.id
        )
        == items2
    )


def test_replace_participants_rejects_too_many(db_session, yard, monkeypatch):
    monkeypatch.setattr(group, "MAX_PARTICIPANTS", 2)
    sess = service.create_session(db_session, yard_id=yard.id, created_by="u-owner")
    ids = [_agent(db_session, yard.id, f"m{i}", f"多{i}", "通用问答") for i in range(3)]
    db_session.commit()
    with pytest.raises(AppError) as e:
        group.replace_participants(db_session, ctx=_ctx(yard.id), session_id=sess.id, agent_ids=ids)
    assert e.value.code == "GROUP_PARTICIPANTS_TOO_MANY" and e.value.http_status == 400
