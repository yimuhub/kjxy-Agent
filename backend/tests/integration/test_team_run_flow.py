"""班组接力协作集成测试（W2，HTTP 端到端，fake provider 全离线）。

覆盖：
1. CRUD 校验：<2 成员 400 / 重名 409 / 跨小院 404 / 停用班组 409；
2. 顺序接力拼接正确：成员输入含「【角色】：输出」段、critic 附审核指令，
   末位 critic 答案=其输出；critic 在中间时答案=末位输出+审核意见并入；
3. 成员失败 → team_run failed 停在断点；retry 只跑失败成员：
   已 ok 成员 0 重跑（其 task_id 下 llm_calls 不再增长、output 不变），
   并从失败成员续到队尾；完成后再 retry → 409；
4. 工具成员断点续跑衔（W1 checkpoint）：失败时已执行的工具副作用不重放，
   retry 以存量 task_id resume（步骤行原位覆盖、sensor.query 计数不变）；
5. 工厂 as_team：装配即组队（蓝图顺序 + 角色名），产出的班组可直接 run；
6. SSE 成员级事件流：completed 运行即时回放 team-init/member-event/team-end。
"""

import pytest
from app.core.security import create_access_token
from app.modules.audit.models import AuditLog
from app.modules.devices import agent_tool
from app.modules.llm import providers_fake
from app.modules.llm.models import LLMCall
from app.modules.tasks import worker_tasks
from app.modules.tools.registry import tool_registry
from app.modules.yards.repository import create_yard
from sqlalchemy.orm import sessionmaker

TEAMS = "/api/v1/agents/teams"
TEAM_RUNS = "/api/v1/agents/team-runs"


def _hdr(user_id: str = "u-team-admin", yard_id: str | None = None):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=["yard_admin"])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _script_isolation():
    providers_fake.reset_scripted_tool_responses()
    yield
    providers_fake.reset_scripted_tool_responses()


@pytest.fixture()
def yard(db_session):
    t = create_yard(db_session, name="班组集成小院")
    db_session.commit()
    return t


@pytest.fixture()
def other_yard(db_session):
    t = create_yard(db_session, name="隔壁班组小院")
    db_session.commit()
    return t


def _mk_published(client, yard, code: str, name: str, tools: list[str] | None = None) -> str:
    """建+发一个低风险声明式助手（knowledge_scopes 必填，小院自批路径）。"""
    created = client.post(
        "/api/v1/agents/declarative",
        json={
            "agent_code": code,
            "name": name,
            "persona_prompt": f"你是{name}。",
            "task_prompt_template": f"请完成{name}任务：{{{{question}}}}",
            "allowed_tools": tools or [],
            "knowledge_scopes": ["yard"],
        },
        headers=_hdr(yard_id=yard.id),
    )
    assert created.status_code == 200, created.text
    agent_id = created.json()["data"]["id"]
    pub = client.post(
        f"/api/v1/agents/declarative/{agent_id}/publish", headers=_hdr(yard_id=yard.id)
    )
    assert pub.json()["data"]["status"] == "published"
    return agent_id


def _mk_team(client, yard, name: str, members: list[dict], **kw):
    return client.post(
        TEAMS, json={"name": name, "members": members, **kw}, headers=_hdr(yard_id=yard.id)
    )


def _three_members(client, yard):
    """解读员 → 诊断员 → 审核员(critic)：三名无工具助手（单轮旧路径）。"""
    a1 = _mk_published(client, yard, "tm_reader", "解读员")
    a2 = _mk_published(client, yard, "tm_diag", "诊断员")
    a3 = _mk_published(client, yard, "tm_critic", "审核员")
    return [
        {"agent_id": a1, "role_name": "解读员"},
        {"agent_id": a2, "role_name": "诊断员"},
        {"agent_id": a3, "role_name": "审核员", "is_critic": True},
    ]


def _run_team(client, yard, team_id, question="棚室温度怎么样"):
    return client.post(
        f"{TEAMS}/{team_id}/run", json={"input": question}, headers=_hdr(yard_id=yard.id)
    )


def _get_run(client, yard, run_id):
    return client.get(f"{TEAM_RUNS}/{run_id}", headers=_hdr(yard_id=yard.id))


# ---------------------------------------------------------------------------
# 1) CRUD 校验
# ---------------------------------------------------------------------------


def test_team_crud_validations(client, yard, other_yard):
    members = _three_members(client, yard)

    # <2 成员 → 400
    thin = _mk_team(client, yard, "薄队", members[:1])
    assert thin.status_code == 400 and thin.json()["error"]["code"] == "TEAM_MEMBER_COUNT"

    ok = _mk_team(client, yard, "棚室管家班组", members, description="接力演示")
    assert ok.status_code == 200, ok.text
    team = ok.json()["data"]
    assert [m["role_name"] for m in team["members"]] == ["解读员", "诊断员", "审核员"]

    # 同小院重名 → 409
    dup = _mk_team(client, yard, "棚室管家班组", members)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "TEAM_NAME_CONFLICT"

    # 跨小院：详情/编辑/删除/运行一律 404（防枚举）
    other_hdr = _hdr("u-other", other_yard.id)
    assert client.get(f"{TEAMS}/{team['id']}", headers=other_hdr).status_code == 404
    assert (
        client.patch(f"{TEAMS}/{team['id']}", json={"name": "窃取改名"}, headers=other_hdr)
    ).status_code == 404
    assert client.delete(f"{TEAMS}/{team['id']}", headers=other_hdr).status_code == 404
    assert _run_team(client, other_yard, team["id"]).status_code == 404

    # 停用 → 运行 409
    off = client.patch(
        f"{TEAMS}/{team['id']}", json={"enabled": False}, headers=_hdr(yard_id=yard.id)
    )
    assert off.json()["data"]["enabled"] is False
    disabled = _run_team(client, yard, team["id"])
    assert disabled.status_code == 409 and disabled.json()["error"]["code"] == "TEAM_DISABLED"
    client.patch(f"{TEAMS}/{team['id']}", json={"enabled": True}, headers=_hdr(yard_id=yard.id))

    # 删除后详情 404（历史列表接口不再可见）
    did = off.json()["data"]["id"]
    assert client.delete(f"{TEAMS}/{did}", headers=_hdr(yard_id=yard.id)).status_code == 200
    assert client.get(f"{TEAMS}/{did}", headers=_hdr(yard_id=yard.id)).status_code == 404


# ---------------------------------------------------------------------------
# 2) 顺序接力 + critic（末位）
# ---------------------------------------------------------------------------


def test_relay_order_and_final_critic_answer(client, yard):
    members = _three_members(client, yard)
    team = _mk_team(client, yard, "接力一队", members).json()["data"]

    providers_fake.set_scripted_tool_responses(
        [{"content": "输出甲"}, {"content": "输出乙"}, {"content": "审核结论：修订丙"}]
    )
    resp = _run_team(client, yard, team["id"])
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "completed"
    # 末位是 critic → 答案 = critic 的修订结论
    assert data["answer"] == "审核结论：修订丙"

    ms = data["members"]
    assert [m["status"] for m in ms] == ["ok", "ok", "ok"]
    assert all(m["task_id"] for m in ms)  # 预置 task_id 全部落库
    # 接力拼接：诊断员输入含解读员段；审核员含前两段 + 审核指令
    assert "【解读员】：输出甲" in ms[1]["input_preview"]
    assert "【解读员】：输出甲" in ms[2]["input_preview"]
    assert "【诊断员】：输出乙" in ms[2]["input_preview"]
    assert "【审核要求】" in ms[2]["input_preview"]

    # 列表 + 详情 + 审计
    runs = client.get(f"{TEAMS}/{team['id']}/runs", headers=_hdr(yard_id=yard.id))
    assert [r["id"] for r in runs.json()["data"]["runs"]] == [data["id"]]
    detail = _get_run(client, yard, data["id"])
    assert (
        detail.json()["data"]["status"] == "completed"
        and len(detail.json()["data"]["members"]) == 3
    )


def test_team_run_audit_written(client, db_session, yard):
    members = _three_members(client, yard)
    team = _mk_team(client, yard, "审计队", members).json()["data"]
    providers_fake.set_scripted_tool_responses(
        [{"content": "A"}, {"content": "B"}, {"content": "C审"}]
    )
    run = _run_team(client, yard, team["id"]).json()["data"]
    row = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "team_run", AuditLog.yard_id == yard.id)
        .first()
    )
    assert row is not None and row.result == "success"
    assert row.metadata_json["team_run_id"] == run["id"]


# ---------------------------------------------------------------------------
# 2b) critic 在中间：答案并入审核意见
# ---------------------------------------------------------------------------


def test_critic_in_middle_merged_into_answer(client, yard):
    a1 = _mk_published(client, yard, "mm_r", "解读员")
    a2 = _mk_published(client, yard, "mm_c", "审核员")
    a3 = _mk_published(client, yard, "mm_s", "汇总员")
    team = _mk_team(
        client,
        yard,
        "接力二队",
        [
            {"agent_id": a1, "role_name": "解读员"},
            {"agent_id": a2, "role_name": "审核员", "is_critic": True},
            {"agent_id": a3, "role_name": "汇总员"},
        ],
    ).json()["data"]
    providers_fake.set_scripted_tool_responses(
        [{"content": "输出甲"}, {"content": "审核意见丙"}, {"content": "最终输出乙"}]
    )
    data = _run_team(client, yard, team["id"]).json()["data"]
    # 末位非 critic → 末位输出 + 审核意见并入（固定拼接策略）
    assert data["answer"] == "最终输出乙\n\n---\n【审核意见·审核员】\n审核意见丙"
    # critic 的输出同样并入后续成员接力上下文
    assert "【审核员】：审核意见丙" in data["members"][2]["input_preview"]


# ---------------------------------------------------------------------------
# 3) 失败停断点 → retry 只跑失败成员
# ---------------------------------------------------------------------------


def test_failure_stops_at_breakpoint_and_retry_skips_ok_members(client, db_session, yard):
    a1 = _mk_published(client, yard, "fr_r", "解读员")
    a2 = _mk_published(client, yard, "fr_d", "诊断员")
    team = _mk_team(
        client,
        yard,
        "断点队",
        [{"agent_id": a1, "role_name": "解读员"}, {"agent_id": a2, "role_name": "诊断员"}],
    ).json()["data"]

    providers_fake.set_scripted_tool_responses(
        [{"content": "成员一输出"}, {"error": {"code": "LLM_ERROR", "message": "上游断了（模拟）"}}]
    )
    failed = _run_team(client, yard, team["id"])
    assert failed.status_code == 502
    assert failed.json()["error"]["code"] == "LLM_ERROR"
    run_id = failed.json()["error"]["details"]["team_run_id"]

    run = _get_run(client, yard, run_id).json()["data"]
    assert run["status"] == "failed" and "LLM_ERROR" in (run["last_error"] or "")
    m1, m2 = run["members"]
    assert m1["status"] == "ok" and m1["output"] == "成员一输出"
    assert m2["status"] == "error" and "LLM_ERROR" in m2["error_text"]
    m1_task, m1_output = m1["task_id"], m1["output"]
    calls_m1_before = db_session.query(LLMCall).filter(LLMCall.task_id == m1_task).count()

    # 修复上游后续跑：只跑失败成员（并续到队尾）
    providers_fake.set_scripted_tool_responses([{"content": "诊断员续跑输出"}])
    retried = client.post(f"{TEAM_RUNS}/{run_id}/retry", headers=_hdr(yard_id=yard.id))
    assert retried.status_code == 200, retried.text
    done = retried.json()["data"]
    assert done["status"] == "completed" and done["answer"] == "诊断员续跑输出"

    n1 = done["members"][0]
    assert n1["status"] == "ok"
    assert n1["output"] == m1_output  # 已 ok 成员输出原样复用
    assert n1["task_id"] == m1_task  # 未新建 run（未重跑）
    assert (
        db_session.query(LLMCall).filter(LLMCall.task_id == m1_task).count() == calls_m1_before
    )  # 关键断言：ok 成员的 task_id 下 0 次新执行

    # retry 审计 + 已完成再 retry → 409
    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "team_run_retry", AuditLog.yard_id == yard.id)
        .first()
    )
    assert audit is not None and audit.result == "success"
    assert audit.metadata_json["skipped_ok_members"] == 1
    assert audit.metadata_json["from_member_index"] == 1
    again = client.post(f"{TEAM_RUNS}/{run_id}/retry", headers=_hdr(yard_id=yard.id))
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "TEAM_RUN_ALREADY_COMPLETED"


# ---------------------------------------------------------------------------
# 4) 工具成员：失败时副作用已发生 → retry 走 W1 checkpoint 不重放
# ---------------------------------------------------------------------------


@pytest.fixture()
def sensor_spy(db_session, monkeypatch):
    """sensor.query 执行计数（同 W1 样板：独立短 session，不污染请求会话）。"""
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    monkeypatch.setattr(agent_tool, "_open_session", factory)
    td = tool_registry.get("sensor.query")
    counter = {"n": 0}
    orig = td.func

    def _counted(params, ctx):
        counter["n"] += 1
        return orig(params, ctx)

    monkeypatch.setattr(td, "func", _counted)
    return counter


def test_tool_member_retry_resumes_checkpoint_without_replay(client, yard, sensor_spy):
    a_tool = _mk_published(client, yard, "ck_reader", "解读员", tools=["sensor.query"])
    a_d = _mk_published(client, yard, "ck_diag", "诊断员")
    a_c = _mk_published(client, yard, "ck_critic", "审核员")
    team = _mk_team(
        client,
        yard,
        "checkpoint 队",
        [
            {"agent_id": a_tool, "role_name": "解读员"},
            {"agent_id": a_d, "role_name": "诊断员"},
            {"agent_id": a_c, "role_name": "审核员", "is_critic": True},
        ],
    ).json()["data"]

    # 首轮：工具调用成功 → 第二次 LLM 失败 → 整队 failed 停在成员 0
    providers_fake.set_scripted_tool_responses(
        [
            {"tool_calls": [{"id": "s1", "name": "sensor__query", "args": {}}]},
            {"error": {"code": "LLM_ERROR", "message": "上游中断（模拟）"}},
        ]
    )
    failed = _run_team(client, yard, team["id"])
    assert failed.status_code == 502
    run_id = failed.json()["error"]["details"]["team_run_id"]
    run = _get_run(client, yard, run_id).json()["data"]
    assert run["status"] == "failed"
    assert run["members"][0]["status"] == "error"
    m1_task = run["members"][0]["task_id"]
    assert sensor_spy["n"] == 1  # 失败前副作用已经发生了一次

    steps_url = f"/api/v1/agents/declarative/runs/{m1_task}/steps"
    shape = [
        (s["step_type"], s["status"])
        for s in client.get(steps_url, headers=_hdr(yard_id=yard.id)).json()["data"]["steps"]
    ]
    assert shape == [("llm_call", "ok"), ("tool_call", "ok"), ("llm_call", "error")]

    # 续跑：成员 0 以存量 task_id resume——成功工具步只回放不执行
    providers_fake.set_scripted_tool_responses(
        [{"content": "续跑读数结论"}, {"content": "诊断意见"}, {"content": "审核通过，无需修订"}]
    )
    retried = client.post(f"{TEAM_RUNS}/{run_id}/retry", headers=_hdr(yard_id=yard.id))
    assert retried.status_code == 200, retried.text
    done = retried.json()["data"]
    assert done["status"] == "completed"
    assert done["answer"] == "审核通过，无需修订"
    assert done["members"][0]["task_id"] == m1_task  # 同一 run 续跑而非新开
    assert sensor_spy["n"] == 1  # 关键断言：副作用工具未重放
    after = client.get(steps_url, headers=_hdr(yard_id=yard.id)).json()["data"]["steps"]
    assert [(s["step_type"], s["status"]) for s in after] == [
        ("llm_call", "ok"),
        ("tool_call", "ok"),
        ("llm_call", "ok"),  # 失败步原位覆盖（无追加重放行）
        ("final", "ok"),
    ]
    assert after[2]["input_json"]["retried"] is True
    # 前序（含续跑成员）输出并入后续接力上下文
    assert "【解读员】：续跑读数结论" in done["members"][1]["input_preview"]


# ---------------------------------------------------------------------------
# 5) 工厂 as_team：装配即组队，班组可直接 run
# ---------------------------------------------------------------------------


@pytest.fixture()
def fac_env(tmp_path):
    """工厂用例独立环境：文件 SQLite + API/worker 各持独立连接（同工厂 API 测试样板）。"""
    from app.core.deps import get_db_session
    from app.db.base import Base
    from app.main import create_app
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine

    engine = create_engine(
        f"sqlite:///{tmp_path / 'team-factory.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    app = create_app()

    def override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_db
    worker_tasks._session_factory = factory
    with TestClient(app, raise_server_exceptions=False) as api_client:
        yield api_client, factory
    worker_tasks._session_factory = None
    engine.dispose()


def test_factory_as_team_builds_runnable_team(fac_env):
    from types import SimpleNamespace

    from app.modules.tasks.models import Task

    api_client, factory = fac_env
    db = factory()
    try:
        t = create_yard(db, name="装配班组小院")
        db.commit()
        yard = SimpleNamespace(id=t.id)
    finally:
        db.close()
    hdr = _hdr("u-fac-team", yard.id)

    gen = api_client.post(
        "/api/v1/agents/factory/generate",
        json={"industry_brief": "设施农业，侧重棚室环境管理"},
        headers=hdr,
    )
    assert gen.status_code == 202, gen.text
    ids = gen.json()["data"]
    worker_tasks.run_task_inline(ids["task_id"])
    tdb = factory()
    try:
        assert tdb.get(Task, ids["task_id"]).status == "succeeded"
    finally:
        tdb.close()

    detail = api_client.get(f"/api/v1/agents/factory/runs/{ids['run_id']}", headers=hdr).json()[
        "data"
    ]
    agents = detail["blueprint_json"]["agents"]
    codes = [a["agent_code"] for a in agents]

    # 采纳 + as_team：装配出的助手按蓝图顺序自动编成班组
    applied = api_client.post(
        f"/api/v1/agents/factory/runs/{ids['run_id']}/apply",
        json={"codes": codes, "publish": True, "as_team": {"name": "装配班组"}},
        headers=hdr,
    )
    assert applied.status_code == 200, applied.text
    payload = applied.json()["data"]
    assert payload["applied"] == len(codes) and payload["team_error"] is None
    team = payload["team"]
    assert team["id"] and [m["role_name"] for m in team["members"]] == [
        a["name"] for a in agents
    ]  # 蓝图顺序 + 蓝图角色名

    team_detail = api_client.get(f"{TEAMS}/{team['id']}", headers=hdr).json()["data"]
    assert len(team_detail["members"]) == len(codes)

    # 装配即产出的班组可直接 run（成员均已 publish）
    providers_fake.set_scripted_tool_responses(
        [{"content": f"接力输出{i}"} for i in range(len(codes))]
    )
    resp = api_client.post(f"{TEAMS}/{team['id']}/run", json={"input": "棚室如何控温"}, headers=hdr)
    assert resp.status_code == 200, resp.text
    run = resp.json()["data"]
    assert run["status"] == "completed"
    assert run["answer"] == f"接力输出{len(codes) - 1}"
    assert all(m["status"] == "ok" for m in run["members"])


# ---------------------------------------------------------------------------
# 6) SSE 成员级事件流
# ---------------------------------------------------------------------------


def test_team_stream_replays_member_events(client, db_session, yard):
    members = _three_members(client, yard)
    team = _mk_team(client, yard, "推流队", members).json()["data"]
    providers_fake.set_scripted_tool_responses(
        [{"content": "A出"}, {"content": "B出"}, {"content": "C审"}]
    )
    run = _run_team(client, yard, team["id"]).json()["data"]

    worker_tasks._session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    try:
        resp = client.get(f"{TEAM_RUNS}/{run['id']}/stream", headers=_hdr(yard_id=yard.id))
        # 未知 run 也在注入窗内：SSE 生成器走全局 sessionmaker，须与测试库同源
        ghost = client.get(f"{TEAM_RUNS}/no-such-run/stream", headers=_hdr(yard_id=yard.id))
    finally:
        worker_tasks._session_factory = None
    assert resp.status_code == 200
    body = resp.text
    assert "event: team-init" in body
    assert body.count("event: member-event") == 3  # 每成员一帧终态
    assert '"agent_run_id": "team-' in body  # 内部步二跳句柄
    assert "event: team-end" in body
    assert '"status": "completed"' in body and '"reason": "run_terminal"' in body

    # 未知 run：SSE 语义不 404，一帧 not_found 收流
    assert "run_not_found" in ghost.text
