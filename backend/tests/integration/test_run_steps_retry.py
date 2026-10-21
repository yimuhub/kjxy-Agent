"""运行 checkpoint 断点续跑集成测试（P0 内核）。

覆盖（HTTP 端到端）：
1. tool_call→text 运行收敛 + GET steps 形状（sensor.query 恰好执行一次）；
2. LLM 中途失败 → steps 状态 failed；retry 从失败步续跑：已 ok 的副作用工具
   **不重放**（execute 计数不变），失败的 LLM 步原位重跑，audit=retry_from_step；
3. 已完成运行 retry → 409；
4. 跨租户访问 steps/retry → 404（防枚举）；
5. SSE 步骤流：completed 运行即时收流（steps-init/step-event.../steps-end）。
"""

import pytest
from app.core.security import create_access_token
from app.modules.audit.models import AuditLog
from app.modules.devices import agent_tool
from app.modules.llm import providers_fake
from app.modules.llm.models import LLMCall
from app.modules.tasks import worker_tasks
from app.modules.tenants.repository import create_tenant
from app.modules.tools.registry import tool_registry
from sqlalchemy.orm import sessionmaker

DOC_Q = "棚室温度怎么样"

BODY = {
    "agent_code": "sensor_helper",
    "name": "传感器问答助手",
    "persona_prompt": "你是设施农业传感器问答助手。",
    "task_prompt_template": "请结合传感器数据回答：{{question}}",
    "allowed_tools": ["sensor.query"],
    "knowledge_scopes": ["tenant"],
}


def _hdr(user_id: str = "u-steps-admin", tenant_id: str | None = None):
    token = create_access_token(user_id=user_id, tenant_id=tenant_id, roles=["tenant_admin"])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _script_isolation():
    providers_fake.reset_scripted_tool_responses()
    yield
    providers_fake.reset_scripted_tool_responses()


@pytest.fixture()
def tenant(db_session):
    t = create_tenant(db_session, name="步骤续跑租户")
    db_session.commit()
    return t


@pytest.fixture()
def other_tenant(db_session):
    t = create_tenant(db_session, name="隔壁步骤租户")
    db_session.commit()
    return t


@pytest.fixture()
def published(client, tenant):
    created = client.post(
        "/api/v1/agents/declarative", json=BODY, headers=_hdr(tenant_id=tenant.id)
    )
    assert created.status_code == 200, created.text
    data = created.json()["data"]
    published = client.post(
        f"/api/v1/agents/declarative/{data['id']}/publish", headers=_hdr(tenant_id=tenant.id)
    )
    assert published.json()["data"]["status"] == "published"
    return data


@pytest.fixture()
def sensor_spy(db_session, monkeypatch):
    """sensor.query 执行计数：工具经 _open_session 通道回注共享内存库。

    工具函数约定自开会话并在 finally 关闭——必须给它独立短 session，
    不能复用请求级 db_session（close 会把请求会话 expunge 掉）。
    """
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


def _run(client, tenant, task_id, question=DOC_Q):
    return client.post(
        "/api/v1/agents/sensor_helper/run",
        json={"input": {"question": question}, "task_id": task_id},
        headers=_hdr(tenant_id=tenant.id),
    )


def _steps(client, tenant, run_id):
    return client.get(
        f"/api/v1/agents/declarative/runs/{run_id}/steps", headers=_hdr(tenant_id=tenant.id)
    )


def _shape(client, tenant, run_id):
    data = _steps(client, tenant, run_id).json()["data"]
    return data["status"], [(s["step_type"], s["status"]) for s in data["steps"]]


# ---------------------------------------------------------------------------
# 1) 端到端：循环收敛 + steps 形状 + 工具执行一次
# ---------------------------------------------------------------------------


def test_tool_loop_run_and_steps_shape(client, tenant, published, sensor_spy):
    providers_fake.set_scripted_tool_responses(
        [
            {"tool_calls": [{"id": "s1", "name": "sensor__query", "args": {}}]},
            {"content": "传感器数据正常"},
        ]
    )
    resp = _run(client, tenant, "run-i1")
    assert resp.status_code == 200, resp.text
    out = resp.json()["data"]["output"]
    assert out["answer"] == "传感器数据正常"
    assert out["run_id"] == "run-i1"
    assert out["truncated"] is False
    assert sensor_spy["n"] == 1  # sensor.query 恰好执行的这一次

    status, shape = _shape(client, tenant, "run-i1")
    assert status == "succeeded"
    assert shape == [
        ("llm_call", "ok"),
        ("tool_call", "ok"),
        ("llm_call", "ok"),
        ("final", "ok"),
    ]


# ---------------------------------------------------------------------------
# 2) 失败 → 断点续跑：已 ok 副作用不重放，失败 LLM 步原位重跑
# ---------------------------------------------------------------------------


def test_retry_from_failed_step_no_side_effect_replay(client, tenant, published, sensor_spy):
    providers_fake.set_scripted_tool_responses(
        [
            {"tool_calls": [{"id": "s1", "name": "sensor__query", "args": {}}]},
            {"error": {"code": "LLM_ERROR", "message": "上游连接中断（模拟）"}},
        ]
    )
    failed = _run(client, tenant, "run-i2")
    assert failed.status_code == 502
    assert failed.json()["error"]["code"] == "LLM_ERROR"
    assert sensor_spy["n"] == 1

    status, shape = _shape(client, tenant, "run-i2")
    assert status == "failed"
    assert shape == [("llm_call", "ok"), ("tool_call", "ok"), ("llm_call", "error")]

    # 修复上游后断点续跑：LLM 步重跑，成功工具步只回放不执行
    providers_fake.set_scripted_tool_responses([{"content": "续跑后拿到了结论"}])
    retried = client.post(
        "/api/v1/agents/declarative/sensor_helper/runs/run-i2/retry",
        headers=_hdr(tenant_id=tenant.id),
    )
    assert retried.status_code == 200, retried.text
    out = retried.json()["data"]["output"]
    assert out["answer"] == "续跑后拿到了结论"
    assert out["run_id"] == "run-i2"
    assert sensor_spy["n"] == 1  # 关键断言：execute 次数不变 → 副作用未重放

    status, shape = _shape(client, tenant, "run-i2")
    assert status == "succeeded"
    assert shape == [
        ("llm_call", "ok"),
        ("tool_call", "ok"),
        ("llm_call", "ok"),
        ("final", "ok"),
    ]  # 失败 LLM 步原位覆盖（索引连续、无重复工具行）

    data = _steps(client, tenant, "run-i2").json()["data"]
    assert data["steps"][2]["input_json"]["retried"] is True
    assert len(data["steps"]) == 4  # 原位覆盖而非追加重放


def test_retry_writes_audit_and_quota_bookkeeping(
    client, db_session, tenant, published, sensor_spy
):
    providers_fake.set_scripted_tool_responses(
        [
            {"tool_calls": [{"id": "s1", "name": "sensor__query", "args": {}}]},
            {"error": {"code": "LLM_ERROR", "message": "模拟中断"}},
        ]
    )
    assert _run(client, tenant, "run-i2b").status_code == 502
    providers_fake.set_scripted_tool_responses([{"content": "续跑完成"}])
    assert (
        client.post(
            "/api/v1/agents/declarative/sensor_helper/runs/run-i2b/retry",
            headers=_hdr(tenant_id=tenant.id),
        )
    ).status_code == 200
    assert sensor_spy["n"] == 1

    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "retry_from_step", AuditLog.tenant_id == tenant.id)
        .first()
    )
    assert audit is not None and audit.result == "success"
    assert audit.metadata_json["from_step_index"] == 2
    assert audit.metadata_json["skipped_tool_side_effects"] == 1
    # 记账：失败尝试 2 次 LLM 调用（1 成 1 败）+ 续跑 1 次 = 3 行
    assert db_session.query(LLMCall).filter(LLMCall.tenant_id == tenant.id).count() == 3


# ---------------------------------------------------------------------------
# 3) 已完成运行拒绝重放；跨租户一律 404
# ---------------------------------------------------------------------------


def test_retry_completed_run_is_409(client, tenant, published, sensor_spy):
    providers_fake.set_scripted_tool_responses([{"content": "一次就收敛"}])
    assert _run(client, tenant, "run-i3").status_code == 200
    resp = client.post(
        "/api/v1/agents/declarative/sensor_helper/runs/run-i3/retry",
        headers=_hdr(tenant_id=tenant.id),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "RUN_ALREADY_COMPLETED"


def test_cross_tenant_steps_and_retry_are_404(client, tenant, other_tenant, published, sensor_spy):
    providers_fake.set_scripted_tool_responses([{"content": "本租户的运行"}])
    assert _run(client, tenant, "run-i4").status_code == 200

    leaked = _steps(client, other_tenant, "run-i4")
    assert leaked.status_code == 404
    assert leaked.json()["error"]["code"] == "RUN_NOT_FOUND"

    retried = client.post(
        "/api/v1/agents/declarative/sensor_helper/runs/run-i4/retry",
        headers=_hdr("u-other", other_tenant.id),
    )
    assert retried.status_code == 404


# ---------------------------------------------------------------------------
# 5) SSE 步骤流：completed 运行 → 全量回放后即时收流
# ---------------------------------------------------------------------------


def test_sse_stream_replays_completed_run(client, db_session, tenant, published, sensor_spy):
    providers_fake.set_scripted_tool_responses(
        [
            {"tool_calls": [{"id": "s1", "name": "sensor__query", "args": {}}]},
            {"content": "流式回放我"},
        ]
    )
    assert _run(client, tenant, "run-i5").status_code == 200

    worker_tasks._session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    try:
        resp = client.get(
            "/api/v1/agents/declarative/runs/run-i5/events/stream",
            headers=_hdr(tenant_id=tenant.id),
        )
    finally:
        worker_tasks._session_factory = None
    assert resp.status_code == 200
    body = resp.text
    assert "event: steps-init" in body
    assert body.count("event: step-event") == 4
    assert '"step_type": "tool_call"' in body
    assert "event: steps-end" in body
    assert '"status": "succeeded"' in body and '"reason": "run_terminal"' in body
