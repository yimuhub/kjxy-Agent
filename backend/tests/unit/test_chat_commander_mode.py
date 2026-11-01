"""对话链路 × 指挥官模式：会话模式落库、关闭降级、计划预览与执行汇总。

这些用例锁的是 chat 侧集成契约（不重复测 commander 内部调度）：
- 会话默认模式跟设置走，读设置失败回落 standard；
- 指挥官被关闭时 commander 请求降级为 standard（响应不撒谎）；
- preview_plan=true 只落计划卡、不触发执行；
- preview_plan=false 执行后落一张 commander_summary 卡，事件名映射成 SSE 帧名。
"""

from types import SimpleNamespace

import pytest
from app.core.deps import YardContext
from app.modules.chat import service
from app.modules.chat.models import ROLE_ASSISTANT
from app.modules.commander import service as commander_service
from app.modules.commander import settings as commander_settings
from app.modules.yards import settings_service
from app.modules.yards.repository import create_yard


@pytest.fixture()
def yard(db_session):
    t = create_yard(db_session, name="指挥官对话小院")
    db_session.commit()
    return t


def _ctx(yard) -> YardContext:
    return YardContext(yard_id=yard.id, user_id="u1", roles=["yard_admin"], request_id="req-1")


def _set_commander(db_session, yard, **overrides):
    value = {**commander_settings.defaults(), **overrides}
    settings_service.update_setting(
        db_session,
        yard_id=yard.id,
        key="commander",
        value=value,
        actor_id="u1",
        request_id="req-1",
    )
    db_session.commit()


def _fake_run(run_id: str = "run-1", tokens: int = 12):
    return SimpleNamespace(id=run_id, tokens_total=tokens, status="queued")


def test_new_session_defaults_to_standard(db_session, yard):
    session = service.create_session(db_session, yard_id=yard.id, created_by="u1")
    db_session.commit()
    assert session.execution_mode == "standard"
    assert service.session_out(db_session, session)["execution_mode"] == "standard"


def test_new_session_default_mode_follows_settings(db_session, yard):
    _set_commander(db_session, yard, enabled=True, default_mode=True)
    session = service.create_session(db_session, yard_id=yard.id, created_by="u1")
    assert session.execution_mode == "commander"


def test_new_session_falls_back_when_commander_disabled(db_session, yard):
    _set_commander(db_session, yard, enabled=False, default_mode=True)
    session = service.create_session(db_session, yard_id=yard.id, created_by="u1")
    assert session.execution_mode == "standard"


def test_apply_execution_mode_downgrades_when_disabled(db_session, yard):
    _set_commander(db_session, yard, enabled=False)
    session = service.create_session(db_session, yard_id=yard.id, created_by="u1")
    assert service.apply_execution_mode(db_session, session, "commander") == "standard"
    _set_commander(db_session, yard, enabled=True)
    assert service.apply_execution_mode(db_session, session, "commander") == "commander"
    # 未传不擅自改：沿用会话当前值
    assert service.apply_execution_mode(db_session, session, None) == "commander"
    # 开关中途关掉：旧会话下一条消息也不能再撞 403，按 standard 跑
    _set_commander(db_session, yard, enabled=False)
    assert service.apply_execution_mode(db_session, session, None) == "standard"
    assert session.execution_mode == "standard"


def test_update_session_flags_execution_mode_downgrade(db_session, yard):
    _set_commander(db_session, yard, enabled=False)
    session = service.create_session(db_session, yard_id=yard.id, created_by="u1")
    service.update_session_flags(
        db_session,
        yard_id=yard.id,
        created_by="u1",
        session_id=session.id,
        execution_mode="commander",
    )
    assert session.execution_mode == "standard"


@pytest.mark.asyncio
async def test_commander_turn_previews_plan_without_executing(db_session, yard, monkeypatch):
    _set_commander(db_session, yard, enabled=True, preview_plan=True)
    session = service.create_session(
        db_session, yard_id=yard.id, created_by="u1", execution_mode="commander"
    )
    db_session.commit()

    run = _fake_run()
    executed: list[str] = []

    async def fake_create_plan(db, *, ctx, session_id, user_content, context=""):
        return run

    def fake_get_run(db, yard_id, run_id):
        return run, []

    def fake_plan_payload(run_obj, subtasks):
        return {
            "type": "commander_plan",
            "run_id": run_obj.id,
            "subtasks": [{"seq": 1, "title": "写大纲"}],
        }

    async def fake_execute(run_id, on_event=None):
        executed.append(run_id)
        return {}

    monkeypatch.setattr(commander_service, "create_plan_run", fake_create_plan)
    monkeypatch.setattr(commander_service, "get_run", fake_get_run)
    monkeypatch.setattr(commander_service, "plan_message_payload", fake_plan_payload)
    monkeypatch.setattr(commander_service, "execute_run", fake_execute)
    monkeypatch.setattr(
        commander_settings,
        "settings_for",
        lambda db, yard_id: {**commander_settings.defaults(), "preview_plan": True},
    )

    events: list[tuple[str, dict]] = []
    outcome = await service.run_and_persist(
        db_session,
        ctx=_ctx(yard),
        session=session,
        content="做一份春耕方案",
        attachments=[],
        execution_mode="commander",
        on_commander_event=lambda kind, payload: events.append((kind, payload)),
    )
    db_session.commit()

    assert executed == []  # 预览模式不执行
    assert outcome["routed_by"] == "commander"
    assert outcome["payload"]["type"] == "commander_plan"
    assert outcome["assistant_message"].role == ROLE_ASSISTANT
    assert outcome["assistant_message"].payload_json["type"] == "commander_plan"
    assert events and events[0][0] == "commander-plan"


@pytest.mark.asyncio
async def test_commander_turn_executes_and_persists_summary(db_session, yard, monkeypatch):
    _set_commander(db_session, yard, enabled=True, preview_plan=False)
    session = service.create_session(
        db_session, yard_id=yard.id, created_by="u1", execution_mode="commander"
    )
    db_session.commit()

    run = _fake_run(tokens=30)
    executed: list[str] = []

    async def fake_create_plan(db, *, ctx, session_id, user_content, context=""):
        return run

    def fake_get_run(db, yard_id, run_id):
        return run, []

    def fake_plan_payload(run_obj, subtasks):
        return {"type": "commander_plan", "run_id": run_obj.id, "subtasks": []}

    def fake_run_out(run_obj, subtasks):
        return {"id": run_obj.id, "status": "completed", "subtasks": []}

    async def fake_execute(run_id, on_event=None):
        executed.append(run_id)
        if on_event is not None:
            on_event("subtask", {"id": "sub-1", "seq": 1, "status": "ok"})
        return {
            "run_id": run_id,
            "answer": "两个子任务均已完成。",
            "artifacts": [{"type": "document", "title": "春耕方案"}],
            "failed": [],
            "tokens_total": 30,
            "degraded": False,
        }

    monkeypatch.setattr(commander_service, "create_plan_run", fake_create_plan)
    monkeypatch.setattr(commander_service, "get_run", fake_get_run)
    monkeypatch.setattr(commander_service, "plan_message_payload", fake_plan_payload)
    monkeypatch.setattr(commander_service, "run_out", fake_run_out)
    monkeypatch.setattr(commander_service, "execute_run", fake_execute)
    monkeypatch.setattr(
        commander_settings,
        "settings_for",
        lambda db, yard_id: {**commander_settings.defaults(), "preview_plan": False},
    )

    events: list[tuple[str, dict]] = []
    outcome = await service.run_and_persist(
        db_session,
        ctx=_ctx(yard),
        session=session,
        content="做一份春耕方案并导出",
        attachments=[],
        execution_mode="commander",
        on_commander_event=lambda kind, payload: events.append((kind, payload)),
    )
    db_session.commit()

    assert executed == [run.id]
    assert outcome["content"] == "两个子任务均已完成。"
    assert outcome["payload"]["type"] == "commander_summary"
    assert outcome["payload"]["artifacts"][0]["title"] == "春耕方案"
    assert outcome["token_usage"]["total_tokens"] == 30
    assert outcome["assistant_message"].payload_json["type"] == "commander_summary"
    assert [kind for kind, _ in events] == ["commander-plan", "subtask-status"]
    # 帧契约：服务层不带 run_id，桥接必须补齐，前端才能按运行归档
    assert all(payload["run_id"] == run.id for _, payload in events)
    assert events[1][1]["subtask_id"]


@pytest.mark.asyncio
async def test_commander_turn_asks_clarifying_questions(db_session, yard, monkeypatch):
    _set_commander(db_session, yard, enabled=True, preview_plan=True, clarify_enabled=True)
    session = service.create_session(
        db_session, yard_id=yard.id, created_by="u1", execution_mode="commander"
    )
    db_session.commit()

    run = _fake_run()
    run.plan_json = {"clarify": ["面向哪些经销商？", "要几页？"], "subtasks": [], "fallback": False}
    executed: list[str] = []
    events: list[tuple[str, dict]] = []

    async def fake_create_plan(db, *, ctx, session_id, user_content, context=""):
        return run

    async def fake_execute(run_id, on_event=None):  # pragma: no cover - 澄清不得执行
        executed.append(run_id)
        raise AssertionError("澄清阶段不应执行")

    monkeypatch.setattr(commander_service, "create_plan_run", fake_create_plan)
    monkeypatch.setattr(commander_service, "execute_run", fake_execute)
    monkeypatch.setattr(
        commander_settings,
        "settings_for",
        lambda db, yard_id: {
            **commander_settings.defaults(),
            "preview_plan": True,
            "clarify_enabled": True,
        },
    )

    outcome = await service.run_and_persist(
        db_session,
        ctx=_ctx(yard),
        session=session,
        content="帮我做个方案",
        attachments=[],
        execution_mode="commander",
        on_commander_event=lambda kind, payload: events.append((kind, payload)),
    )
    db_session.commit()

    assert executed == []
    assert events == []  # 不推计划卡，只回一条提问消息
    assert outcome["reason"] == "指挥官澄清"
    assert outcome["payload"] is None
    text = outcome["assistant_message"].content
    assert "面向哪些经销商？" in text and "要几页？" in text
    assert outcome["assistant_message"].payload_json in (None, {})


@pytest.mark.asyncio
async def test_commander_clarify_disabled_falls_back_to_plan(db_session, yard, monkeypatch):
    _set_commander(db_session, yard, enabled=True, preview_plan=True, clarify_enabled=False)
    session = service.create_session(
        db_session, yard_id=yard.id, created_by="u1", execution_mode="commander"
    )
    db_session.commit()

    run = _fake_run()
    run.plan_json = {"clarify": ["要不要问？"], "subtasks": [], "fallback": True}

    async def fake_create_plan(db, *, ctx, session_id, user_content, context=""):
        return run

    def fake_get_run(db, yard_id, run_id):
        return run, []

    def fake_plan_payload(run_obj, subtasks):
        return {"type": "commander_plan", "run_id": run_obj.id, "subtasks": []}

    monkeypatch.setattr(commander_service, "create_plan_run", fake_create_plan)
    monkeypatch.setattr(commander_service, "get_run", fake_get_run)
    monkeypatch.setattr(commander_service, "plan_message_payload", fake_plan_payload)
    monkeypatch.setattr(
        commander_settings,
        "settings_for",
        lambda db, yard_id: {
            **commander_settings.defaults(),
            "preview_plan": True,
            "clarify_enabled": False,
        },
    )

    outcome = await service.run_and_persist(
        db_session,
        ctx=_ctx(yard),
        session=session,
        content="帮我做个方案",
        attachments=[],
        execution_mode="commander",
    )
    assert outcome["payload"]["type"] == "commander_plan"


@pytest.mark.asyncio
async def test_full_control_skips_clarify_and_executes(db_session, yard, monkeypatch):
    """完全控制 = 不问：澄清与计划确认都跳过，直接开跑。"""
    _set_commander(db_session, yard, enabled=True, preview_plan=True, clarify_enabled=True)
    session = service.create_session(
        db_session, yard_id=yard.id, created_by="u1", execution_mode="commander"
    )
    session.approval_mode = "full"
    db_session.commit()

    run = _fake_run()
    run.plan_json = {"clarify": ["要几页？"], "subtasks": [], "fallback": False}
    executed: list[str] = []

    async def fake_create_plan(db, *, ctx, session_id, user_content, context=""):
        return run

    def fake_get_run(db, yard_id, run_id):
        return run, []

    def fake_plan_payload(run_obj, subtasks):
        return {"type": "commander_plan", "run_id": run_obj.id, "subtasks": []}

    def fake_run_out(run_obj, subtasks):
        return {"id": run_obj.id, "status": "completed", "subtasks": []}

    async def fake_execute(run_id, on_event=None):
        executed.append(run_id)
        return {
            "run_id": run_id,
            "answer": "已按假设完成。",
            "artifacts": [],
            "failed": [],
            "tokens_total": 1,
            "degraded": False,
        }

    monkeypatch.setattr(commander_service, "create_plan_run", fake_create_plan)
    monkeypatch.setattr(commander_service, "get_run", fake_get_run)
    monkeypatch.setattr(commander_service, "plan_message_payload", fake_plan_payload)
    monkeypatch.setattr(commander_service, "run_out", fake_run_out)
    monkeypatch.setattr(commander_service, "execute_run", fake_execute)
    monkeypatch.setattr(
        commander_settings,
        "settings_for",
        lambda db, yard_id: {
            **commander_settings.defaults(),
            "preview_plan": True,
            "clarify_enabled": True,
        },
    )

    outcome = await service.run_and_persist(
        db_session,
        ctx=_ctx(yard),
        session=session,
        content="帮我做个方案",
        attachments=[],
        execution_mode="commander",
    )
    db_session.commit()

    assert executed == [run.id]
    assert outcome["payload"]["type"] == "commander_summary"


@pytest.mark.asyncio
async def test_commander_turn_injects_recent_artifacts_for_feedback(db_session, yard, monkeypatch):
    """「第 3 页不好」这类反馈要带上上一版产物标题，否则会被当成新任务重做。"""
    from app.modules.chat.models import ChatMessage

    _set_commander(db_session, yard, enabled=True, preview_plan=True)
    session = service.create_session(
        db_session, yard_id=yard.id, created_by="u1", execution_mode="commander"
    )
    db_session.add(
        ChatMessage(
            session_id=session.id,
            yard_id=yard.id,
            seq=1,
            role="assistant",
            content="已完成初稿：春耕方案共 8 页。",
            status="ok",
            routed_by="commander",
            payload_json={
                "type": "commander_summary",
                "artifacts": [{"type": "document", "title": "春耕方案 v1"}],
            },
        )
    )
    db_session.add(
        ChatMessage(
            session_id=session.id,
            yard_id=yard.id,
            seq=2,
            role="user",
            content="第 3 页排版不好，改一下",
            status="ok",
        )
    )
    db_session.commit()

    run = _fake_run()
    captured: dict[str, str] = {}

    async def fake_create_plan(db, *, ctx, session_id, user_content, context=""):
        captured["context"] = context
        return run

    def fake_get_run(db, yard_id, run_id):
        return run, []

    def fake_plan_payload(run_obj, subtasks):
        return {"type": "commander_plan", "run_id": run_obj.id, "subtasks": []}

    monkeypatch.setattr(commander_service, "create_plan_run", fake_create_plan)
    monkeypatch.setattr(commander_service, "get_run", fake_get_run)
    monkeypatch.setattr(commander_service, "plan_message_payload", fake_plan_payload)
    monkeypatch.setattr(
        commander_settings,
        "settings_for",
        lambda db, yard_id: {**commander_settings.defaults(), "preview_plan": True},
    )

    await service.run_and_persist(
        db_session,
        ctx=_ctx(yard),
        session=session,
        content="第 3 页排版不好，改一下",
        attachments=[],
        execution_mode="commander",
    )

    context = captured["context"]
    assert "上一版产物：春耕方案 v1" in context
    # 当前这条消息由 user_content 单独传，不重复塞进上下文
    assert "第 3 页排版不好" not in context
