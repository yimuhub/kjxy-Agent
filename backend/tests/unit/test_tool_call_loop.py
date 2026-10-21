"""工具循环单元测试（P0 内核）：fake provider 脚本回放 → 循环收敛/checkpoint 形状。

覆盖：
1. 先 tool_call 后文本 → run 收敛、sensor/echo 工具恰好执行一次、steps 表形状正确；
2. 工具执行失败 → 错误文本以 role=tool 回填、循环继续、运行照常收敛；
3. max_iterations 超限 → truncated=true 诚实标注（final 步 + warning）；
4. 不支持工具的供应商（supports_tools=false）→ 不发 tools、忽略 tool_calls，零回归；
5. 风险闸在循环内生效：高风险未批 → TOOL_RISK_BLOCKED 回填拒绝路径，实现不执行；
6. token 预算护栏；模型幻觉未授权工具 → TOOL_NOT_ALLOWED 拒绝回填。

全部离线：LLM 走 fake provider（脚本注入），工具用测试专用注册项。
"""

import pytest
from app.core.config import settings
from app.core.deps import TenantContext
from app.modules.agents import declarative
from app.modules.agents import run_steps as rs
from app.modules.agents.models import STATUS_PUBLISHED, TenantAgent
from app.modules.agents.run_steps import AgentRunStep
from app.modules.llm import providers_fake
from app.modules.llm.models import LLMCall
from app.modules.llm.providers_fake import FakeProvider
from app.modules.llm.service import complete_with_logging
from app.modules.tenants.repository import create_tenant
from app.modules.tools.models import ToolApproval
from app.modules.tools.registry import ToolDef, tool_registry


@pytest.fixture(autouse=True)
def _script_isolation():
    """fake provider 脚本是进程级状态：每条用例前后强制清空，防串测。"""
    providers_fake.reset_scripted_tool_responses()
    yield
    providers_fake.reset_scripted_tool_responses()


@pytest.fixture()
def tenant(db_session):
    t = create_tenant(db_session, name="工具循环租户")
    db_session.commit()
    return t


def _ctx(tenant_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id, user_id="u-loop", roles=["tenant_admin"], request_id="req-loop"
    )


def _make_agent(db, tenant_id: str, tools: list[str], *, code: str = "loop_agent") -> TenantAgent:
    agent = TenantAgent(
        tenant_id=tenant_id,
        agent_code=code,
        name="循环测试助手",
        persona_prompt="你是工具测试助手。",
        task_prompt_template="请回答：{{question}}",
        allowed_tools=list(tools),
        knowledge_scopes=["tenant"],
        status=STATUS_PUBLISHED,
        risk_level="low",
        version="1.0.0",
    )
    db.add(agent)
    db.commit()
    return agent


def _register_tool(db_session, code: str, func, *, risk: str = "read_only") -> ToolDef:
    """注册测试专用工具；teardown 由 monkeypatch 式手工快照恢复。"""
    td = ToolDef(
        code=code,
        version="1.0.0",
        name=f"测试工具 {code}",
        description="单元测试专用工具",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "additionalProperties": False,
        },
        func=func,
        risk_level=risk,
        side_effect=risk == "external_side_effect",
    )
    saved = tool_registry._tools.get(code)
    tool_registry._tools[code] = td

    def _restore() -> None:
        tool_registry._tools.pop(code, None)
        if saved is not None:
            tool_registry._tools[code] = saved

    _restore_hooks.append(_restore)
    return td


_restore_hooks: list = []


@pytest.fixture(autouse=True)
def _tool_registry_cleanup():
    yield
    while _restore_hooks:
        _restore_hooks.pop()()


@pytest.fixture()
def echo_tool():
    """低风险回显工具（loop.echo → wire 名 loop__echo）。返回调用计数 dict。"""
    calls = {"n": 0}

    def _func(params: dict, ctx: TenantContext) -> dict:
        calls["n"] += 1
        return {"echo": str(params.get("text") or ""), "exec_no": calls["n"]}

    _register_tool(None, "loop.echo", _func)
    return calls


# ---------------------------------------------------------------------------
# 1) 先 tool_call 后文本：收敛 + 执行一次 + steps 形状
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_then_text_converges(db_session, tenant, echo_tool):
    agent = _make_agent(db_session, tenant.id, ["loop.echo"])
    providers_fake.set_scripted_tool_responses(
        [
            {"tool_calls": [{"id": "c1", "name": "loop__echo", "args": {"text": "hello"}}]},
            {"content": "根据工具结果：hello"},
        ]
    )
    result = await declarative.run_declarative_agent(
        db_session,
        agent=agent,
        input_data={"question": "测试工具", "top_k": 2},
        ctx=_ctx(tenant.id),
        task_id="run-u1",
    )
    assert result.status == "succeeded"
    assert result.output["answer"] == "根据工具结果：hello"
    assert result.output["run_id"] == "run-u1"
    assert result.output["truncated"] is False
    assert echo_tool["n"] == 1  # 恰好执行一次

    steps = rs.list_steps(db_session, tenant_id=tenant.id, run_id="run-u1")
    assert [(s.step_type, s.status) for s in steps] == [
        ("llm_call", "ok"),
        ("tool_call", "ok"),
        ("llm_call", "ok"),
        ("final", "ok"),
    ]
    first, tool_row = steps[0], steps[1]
    # 首步行携带 base 提示词 + 运行入参（断点续跑精确重建的依据）
    assert first.input_json["base"]["system"] and first.input_json["base"]["user"]
    assert first.input_json["input"]["question"] == "测试工具"
    assert first.output_json["tool_calls"] == [
        {"id": "c1", "name": "loop__echo", "args_json": '{"text": "hello"}'}
    ]
    assert tool_row.tool_code == "loop.echo"
    assert tool_row.input_json["tool_call_id"] == "c1"
    assert tool_row.output_json["echo"] == "hello"
    # LLM 记账：两次调用（tool_call 轮 + 文本轮）
    assert db_session.query(LLMCall).filter(LLMCall.tenant_id == tenant.id).count() == 2


# ---------------------------------------------------------------------------
# 2) 工具失败：错误回填后循环继续
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_failure_backfilled_and_loop_continues(db_session, tenant, monkeypatch):
    def _boom(params: dict, ctx: TenantContext) -> dict:
        raise RuntimeError("kaboom")

    _register_tool(None, "loop.boom", _boom)
    agent = _make_agent(db_session, tenant.id, ["loop.boom"], code="loop_boom")
    providers_fake.set_scripted_tool_responses(
        [
            {"tool_calls": [{"id": "b1", "name": "loop__boom", "args": {}}]},
            {"content": "工具失败了，但我如实说明"},
        ]
    )
    # 记录第二次 LLM 请求：工具失败必须以 role=tool 错误消息回填进会话
    seen: list = []
    orig = complete_with_logging

    async def _spy(db, request):
        seen.append(request)
        return await orig(db, request)

    monkeypatch.setattr("app.modules.llm.service.complete_with_logging", _spy)

    result = await declarative.run_declarative_agent(
        db_session,
        agent=agent,
        input_data={"question": "会让工具爆炸的问题"},
        ctx=_ctx(tenant.id),
        task_id="run-u2",
    )
    assert result.status == "succeeded"  # 工具失败不中断运行
    assert result.output["answer"] == "工具失败了，但我如实说明"
    steps = rs.list_steps(db_session, tenant_id=tenant.id, run_id="run-u2")
    assert [(s.step_type, s.status) for s in steps] == [
        ("llm_call", "ok"),
        ("tool_call", "error"),
        ("llm_call", "ok"),
        ("final", "ok"),
    ]
    # tool_registry.execute 把实现异常包装成 INTERNAL_ERROR，错误文本回填继续循环
    assert steps[1].error_text is not None and steps[1].error_text.startswith("INTERNAL_ERROR")
    tool_msgs = [m for m in seen[1].messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert "INTERNAL_ERROR" in (tool_msgs[0].content or "")


# ---------------------------------------------------------------------------
# 3) max_iterations 截断：诚实标注 truncated=true
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_iterations_marks_truncated(db_session, tenant, echo_tool, monkeypatch):
    monkeypatch.setattr(settings, "agent_tool_loop_max_iterations", 3)
    agent = _make_agent(db_session, tenant.id, ["loop.echo"], code="loop_agent3")
    providers_fake.set_scripted_tool_responses(
        [
            {"tool_calls": [{"id": f"c{i}", "name": "loop__echo", "args": {"text": str(i)}}]}
            for i in range(10)
        ]
    )
    result = await declarative.run_declarative_agent(
        db_session,
        agent=agent,
        input_data={"question": "无限循环 tendencies"},
        ctx=_ctx(tenant.id),
        task_id="run-u3",
    )
    assert result.output["truncated"] is True
    assert any("最大轮数" in w for w in result.warnings)
    assert echo_tool["n"] == 3  # 恰好跑满 3 轮，不再发起第 4 轮 LLM 调用
    steps = rs.list_steps(db_session, tenant_id=tenant.id, run_id="run-u3")
    final = [s for s in steps if s.step_type == "final"]
    assert len(final) == 1 and final[0].status == "ok"
    assert final[0].output_json["truncated"] is True
    assert final[0].output_json["truncate_reason"] == "iterations"
    assert sum(1 for s in steps if s.step_type == "llm_call") == 3


# ---------------------------------------------------------------------------
# 4) 非工具供应商：零回归
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_without_tools_is_zero_regression(
    db_session, tenant, echo_tool, monkeypatch
):
    monkeypatch.setattr(FakeProvider, "supports_tools", False)
    agent = _make_agent(db_session, tenant.id, ["loop.echo"], code="loop_agent4")
    # 即便 provider 反常返回 tool_calls，不支持能力的供应商路径也必须忽略
    providers_fake.set_scripted_tool_responses(
        [{"content": "纯文本回答", "tool_calls": [{"name": "loop__echo", "args": {"text": "x"}}]}]
    )
    seen: list = []
    orig = complete_with_logging

    async def _spy(db, request):
        seen.append(request)
        return await orig(db, request)

    monkeypatch.setattr("app.modules.llm.service.complete_with_logging", _spy)

    result = await declarative.run_declarative_agent(
        db_session,
        agent=agent,
        input_data={"question": "无能力供应商问题"},
        ctx=_ctx(tenant.id),
        task_id="run-u4",
    )
    assert result.output["answer"] == "纯文本回答"
    assert result.output["truncated"] is False
    assert echo_tool["n"] == 0  # 工具一次都不执行
    assert seen[0].tools is None  # 发给 provider 的请求不含 tools（与旧路径一致）
    assert db_session.query(LLMCall).filter(LLMCall.tenant_id == tenant.id).count() == 1
    steps = rs.list_steps(db_session, tenant_id=tenant.id, run_id="run-u4")
    assert [(s.step_type, s.status) for s in steps] == [("llm_call", "ok"), ("final", "ok")]
    assert seen[0].messages[-1].role == "user"  # 无 tool 回填消息混入


# ---------------------------------------------------------------------------
# 5) 风险闸在循环内生效（未批高风险 → 拒绝回填，实现绝不执行）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_risk_gate_blocks_high_risk_tool_inside_loop(db_session, tenant):
    fired = {"n": 0}

    def _danger(params: dict, ctx: TenantContext) -> dict:
        fired["n"] += 1  # 绝不允许被执行
        return {"dispatched": True}

    _register_tool(None, "loop.danger", _danger, risk="external_side_effect")
    agent = _make_agent(db_session, tenant.id, ["loop.danger"], code="loop_danger")
    providers_fake.set_scripted_tool_responses(
        [
            {"tool_calls": [{"id": "d1", "name": "loop__danger", "args": {}}]},
            {"content": "该操作需人工审批，我无法执行"},
        ]
    )
    result = await declarative.run_declarative_agent(
        db_session,
        agent=agent,
        input_data={"question": "把工单发出去"},
        ctx=_ctx(tenant.id),
        task_id="run-u5",
    )
    assert result.status == "succeeded"
    assert fired["n"] == 0
    steps = rs.list_steps(db_session, tenant_id=tenant.id, run_id="run-u5")
    tool_rows = [s for s in steps if s.step_type == "tool_call"]
    assert len(tool_rows) == 1 and tool_rows[0].status == "error"
    assert "TOOL_RISK_BLOCKED" in (tool_rows[0].error_text or "")
    # registry 风险闸自动落 pending 审批单（拒绝路径可走审批流）
    pending = (
        db_session.query(ToolApproval)
        .filter(ToolApproval.tenant_id == tenant.id, ToolApproval.tool_code == "loop.danger")
        .all()
    )
    assert len(pending) == 1 and pending[0].status == "pending"


# ---------------------------------------------------------------------------
# 6) token 预算护栏 + 幻觉未授权工具拒绝
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_budget_marks_truncated(db_session, tenant, echo_tool, monkeypatch):
    monkeypatch.setattr(settings, "agent_tool_loop_max_tokens", 50)  # fake 按字符估算，必然超限
    agent = _make_agent(db_session, tenant.id, ["loop.echo"], code="loop_agent6")
    providers_fake.set_scripted_tool_responses(
        [
            {"tool_calls": [{"id": "c1", "name": "loop__echo", "args": {"text": "one"}}]},
            {"content": "不该被走到"},
        ]
    )
    result = await declarative.run_declarative_agent(
        db_session,
        agent=agent,
        input_data={"question": "预算护栏"},
        ctx=_ctx(tenant.id),
        task_id="run-u6",
    )
    assert result.output["truncated"] is True
    assert any("token 预算" in w for w in result.warnings)
    steps = rs.list_steps(db_session, tenant_id=tenant.id, run_id="run-u6")
    final = next(s for s in steps if s.step_type == "final")
    assert final.output_json["truncate_reason"] == "tokens"
    assert db_session.query(LLMCall).filter(LLMCall.tenant_id == tenant.id).count() == 1


@pytest.mark.asyncio
async def test_hallucinated_tool_rejected_by_whitelist(db_session, tenant, echo_tool):
    agent = _make_agent(db_session, tenant.id, ["loop.echo"], code="loop_agent7")
    providers_fake.set_scripted_tool_responses(
        [
            {"tool_calls": [{"id": "x1", "name": "loop__ghost", "args": {}}]},
            {"content": "该工具不可用，我改用已有信息回答"},
        ]
    )
    result = await declarative.run_declarative_agent(
        db_session,
        agent=agent,
        input_data={"question": "调用不存在的工具"},
        ctx=_ctx(tenant.id),
        task_id="run-u7",
    )
    assert result.output["answer"] == "该工具不可用，我改用已有信息回答"
    assert echo_tool["n"] == 0
    steps = rs.list_steps(db_session, tenant_id=tenant.id, run_id="run-u7")
    tool_rows = [s for s in steps if s.step_type == "tool_call"]
    assert len(tool_rows) == 1 and tool_rows[0].status == "error"
    assert "TOOL_NOT_ALLOWED" in (tool_rows[0].error_text or "")


def test_step_row_shape_helper(db_session, tenant):
    """record_step 行形状冒烟：字段齐全、租户边界生效。"""
    row = rs.record_step(
        db_session,
        tenant_id=tenant.id,
        run_id="run-shape",
        step_type=rs.STEP_TOOL,
        tool_code="loop.echo",
        input_json={"tool_call_id": "z1"},
        output_json={"ok": True},
        duration_ms=12,
    )
    assert isinstance(row, AgentRunStep) and row.step_index == 0
    assert rs.list_steps(db_session, tenant_id="other-tenant", run_id="run-shape") == []
    out = rs.step_out(row)
    assert out["step_type"] == "tool_call" and out["duration_ms"] == 12
