"""v1.3-B 自生长闭环集成测试（全离线，fake provider + 造数据）。

C1 skills/suggest：证据不足诚实拒绝 / 候选卡落 pending_review / LLM 不可用不落半成品
/ 复用现有审核队列 / 角色闸。
C2 plugins：register 校验（步骤超限/未知与 mcp 工具 400）→ review 发布闸 →
run 解释执行（模板插值/save_as）→ 高风险工具运行闸兜底（TOOL_RISK_BLOCKED）→
suggest 重复序列挖掘（幂等、只产草稿）→ created_from 与小院边界。
"""

import json

import pytest
from app.core.deps import get_db_session
from app.core.security import create_access_token
from app.main import create_app
from app.modules.agents.run_steps import (
    STEP_FINAL,
    STEP_LLM,
    STEP_OK,
    STEP_TOOL,
    record_step,
)
from app.modules.llm import providers_fake
from app.modules.plugins import service as plugin_service
from app.modules.tools.registry import ToolDef, tool_registry
from fastapi.testclient import TestClient

YARD_A = "yard-a"
YARD_B = "yard-b"


# ---------------------------------------------------------------- fixtures
@pytest.fixture()
def growth_client(db_session):
    app = create_app()

    def override_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db_session] = override_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_llm_script():
    yield
    providers_fake.reset_scripted_tool_responses()


@pytest.fixture()
def wf_tools():
    """注册测试临时工具；结束后恢复注册表快照（进程级状态必须还原，防串测）。"""
    saved = dict(tool_registry._tools)  # noqa: SLF001 - 测试专用快照恢复

    def _echo(params, _ctx):  # noqa: ANN001, ANN202
        return {"echo": params.get("text", ""), "ok": True}

    def _notify(params, _ctx):  # noqa: ANN001, ANN202
        return {"notified": bool(params), "ok": True}

    def _danger(_params, _ctx):  # noqa: ANN001, ANN202
        return {"dispatched": True}

    tool_registry.register(
        ToolDef(
            code="wf.test.echo",
            version="1.0.0",
            name="测试回显",
            description="测试工具",
            input_schema={"type": "object"},
            func=_echo,
            risk_level="read_only",
        )
    )
    tool_registry.register(
        ToolDef(
            code="wf.test.notify",
            version="1.0.0",
            name="测试通知",
            description="测试工具",
            input_schema={"type": "object"},
            func=_notify,
            risk_level="read_only",
        )
    )
    tool_registry.register(
        ToolDef(
            code="wf.test.dispatch",
            version="1.0.0",
            name="测试高危下发",
            description="external_side_effect 测试工具",
            input_schema={"type": "object"},
            func=_danger,
            risk_level="external_side_effect",
            side_effect=True,
        )
    )
    yield
    tool_registry._tools.clear()  # noqa: SLF001
    tool_registry._tools.update(saved)


# ---------------------------------------------------------------- helpers
def _headers(user_id: str = "u-1", yard: str = YARD_A, roles=("yard_admin",)) -> dict:
    token = create_access_token(user_id=user_id, yard_id=yard, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


def _seed_run(
    db,  # noqa: ANN001
    *,
    yard: str = YARD_A,
    run_id: str,
    tools: tuple[str, ...] = (),
    question: str | None = None,
    fail: bool = False,
    final: bool = True,
) -> None:
    """造 agent_run_steps 台账：llm(入参) → tools… → (错误步) → final。"""
    if question is not None:
        record_step(
            db,
            yard_id=yard,
            run_id=run_id,
            step_type=STEP_LLM,
            status=STEP_OK,
            input_json={"question": question},
            commit=False,
        )
    for code in tools:
        record_step(
            db,
            yard_id=yard,
            run_id=run_id,
            step_type=STEP_TOOL,
            status=STEP_OK,
            tool_code=code,
            input_json={"args": {}},
            commit=False,
        )
    if fail:
        record_step(
            db,
            yard_id=yard,
            run_id=run_id,
            step_type=STEP_TOOL,
            status="error",
            tool_code="wf.test.echo",
            error_text="TOOL_TIMEOUT: 上游传感器无响应",
            commit=False,
        )
    if final:
        record_step(
            db,
            yard_id=yard,
            run_id=run_id,
            step_type=STEP_FINAL,
            status=STEP_OK,
            commit=False,
        )
    db.commit()


def _cards_payload() -> str:
    return json.dumps(
        [
            {
                "title": "温室湿度异常巡检技能",
                "when_to_use": "传感器湿度连续越限需要巡检建议时",
                "procedure": ["读取近 24 小时湿度读数", "对照规则阈值", "生成处置建议"],
                "examples": ["湿度 92% 持续 3 小时怎么办"],
            },
            {
                "title": "灌溉工单模板技能",
                "when_to_use": "需要批量下发灌溉工单时",
                "procedure": ["确认地块与面积", "套用灌溉模板", "提交审批"],
                "examples": [],
            },
        ],
        ensure_ascii=False,
    )


def _manifest(plugin_id: str = "wf.demo", **overrides) -> dict:
    manifest = {
        "plugin_id": plugin_id,
        "name": "演示工作流",
        "description": "查读数并给建议",
        "params": [{"key": "q", "label": "问题", "required": True}],
        "steps": [
            {
                "kind": "tool",
                "tool": "wf.test.echo",
                "args": {"text": "{{q}}"},
                "save_as": "r1",
            },
            {"kind": "llm", "prompt": "基于 {{r1}} 回答 {{q}}", "save_as": "answer"},
        ],
    }
    manifest.update(overrides)
    return manifest


# ================================================================ C1 skills/suggest
def test_skill_suggest_insufficient_evidence_honest_reject(growth_client, db_session):
    for i in range(4):  # 4 < 5：诚实拒绝，不硬凑
        _seed_run(db_session, run_id=f"r-{i}", tools=("wf.test.echo",), question=f"问题{i}")
    resp = growth_client.post("/api/v1/skills/suggest", json={}, headers=_headers())
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "EVOLUTION_INSUFFICIENT_EVIDENCE"
    # 未落任何半成品
    listing = growth_client.get("/api/v1/skills", headers=_headers()).json()["data"]
    assert listing == []


def test_skill_suggest_creates_pending_review_drafts(growth_client, db_session):
    for i in range(6):
        _seed_run(
            db_session,
            run_id=f"r-{i}",
            tools=("wf.test.echo", "wf.test.notify"),
            question=f"湿度高怎么办{i}",
            fail=(i == 5),
        )
    providers_fake.set_scripted_tool_responses([{"content": _cards_payload()}])
    resp = growth_client.post(
        "/api/v1/skills/suggest", json={"focus_hint": "关注湿度"}, headers=_headers()
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()["data"]
    assert body["created"] == 2
    first = body["skills"][0]
    assert first["status"] == "pending_review"
    assert first["created_from"] == "auto:suggest"
    assert first["skill_code"].startswith("auto-")
    definition = first["definition"]
    assert definition["title"] == "温室湿度异常巡检技能"
    assert len(definition["procedure"]) == 3
    evidence = definition["evidence"]
    assert evidence["run_count"] == 6
    assert 0 < evidence["success_rate"] < 1
    assert "wf.test.echo" in evidence["top_tools"]
    assert evidence["sample_tasks"]
    # 审核队列零改造：pending_review 立即可见，approve 即发布（无 evaluation → 无门禁）
    listing = growth_client.get(
        "/api/v1/skills", params={"status": "pending_review"}, headers=_headers()
    ).json()["data"]
    assert {s["id"] for s in body["skills"]} == {s["id"] for s in listing}
    resp = growth_client.post(
        f"/api/v1/skills/{first['id']}/review",
        json={"action": "approve", "note": "采纳"},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "published"


def test_skill_suggest_llm_unavailable_no_partial(growth_client, db_session):
    for i in range(6):
        _seed_run(db_session, run_id=f"r-{i}", tools=("wf.test.echo",), question=f"q{i}")
    providers_fake.set_scripted_tool_responses(
        [{"error": {"code": "LLM_ERROR", "message": "上游不可用"}}]
    )
    resp = growth_client.post("/api/v1/skills/suggest", json={}, headers=_headers())
    assert resp.status_code == 502, resp.text
    assert resp.json()["error"]["code"] == "EVOLUTION_LLM_UNAVAILABLE"
    listing = growth_client.get("/api/v1/skills", headers=_headers()).json()["data"]
    assert listing == []  # 不落半成品


def test_skill_suggest_requires_yard_admin(growth_client, db_session):
    for i in range(6):
        _seed_run(db_session, run_id=f"r-{i}", tools=("wf.test.echo",))
    resp = growth_client.post(
        "/api/v1/skills/suggest",
        json={},
        headers=_headers("u-2", roles=("member",)),
    )
    assert resp.status_code == 403, resp.text


# ================================================================ C2 plugins register/review/run
def test_plugin_register_validation(growth_client, wf_tools):
    member = _headers("u-2", roles=("member",))
    bad_tool = _manifest("wf.bad", steps=[{"kind": "tool", "tool": "no.such.tool"}])
    resp = growth_client.post(
        "/api/v1/plugins/register", json={"manifest": bad_tool}, headers=_headers()
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "PLUGIN_MANIFEST_INVALID"

    mcp_step = _manifest("wf.mcp", steps=[{"kind": "tool", "tool": "mcp__server__tool"}])
    resp = growth_client.post(
        "/api/v1/plugins/register", json={"manifest": mcp_step}, headers=_headers()
    )
    assert resp.status_code == 400, resp.text

    too_long = _manifest(
        "wf.long",
        steps=[{"kind": "tool", "tool": "wf.test.echo"} for _ in range(9)],
    )
    resp = growth_client.post(
        "/api/v1/plugins/register", json={"manifest": too_long}, headers=_headers()
    )
    assert resp.status_code == 400, resp.text

    resp = growth_client.post(
        "/api/v1/plugins/register",
        json={"manifest": _manifest()},
        headers=member,
    )
    assert resp.status_code == 403, resp.text

    resp = growth_client.post(
        "/api/v1/plugins/register", json={"manifest": _manifest()}, headers=_headers()
    )
    assert resp.status_code == 201, resp.text
    plugin = resp.json()["data"]
    assert plugin["status"] == "pending_review"
    assert plugin["type"] == "workflow"
    assert plugin["created_from"] == "manual"
    assert plugin["plugin_id"] == "wf.demo"


def test_plugin_publish_gate_and_run(growth_client, wf_tools):
    resp = growth_client.post(
        "/api/v1/plugins/register", json={"manifest": _manifest()}, headers=_headers()
    )
    assert resp.status_code == 201, resp.text

    # 发布闸：未审核草稿不可运行
    resp = growth_client.post(
        "/api/v1/plugins/wf.demo/run", json={"params": {"q": "浇多少水"}}, headers=_headers()
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "PLUGIN_NOT_ENABLED"

    resp = growth_client.post(
        "/api/v1/plugins/wf.demo/review",
        json={"decision": "approve"},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "enabled"
    # 重复审核 → 409
    resp = growth_client.post(
        "/api/v1/plugins/wf.demo/review",
        json={"decision": "approve"},
        headers=_headers(),
    )
    assert resp.status_code == 409, resp.text

    providers_fake.set_scripted_tool_responses([{"content": "建议：小水勤浇，保持土壤湿润。"}])
    resp = growth_client.post(
        "/api/v1/plugins/wf.demo/run", json={"params": {"q": "浇多少水"}}, headers=_headers()
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["data"]
    assert result["final"] == "建议：小水勤浇，保持土壤湿润。"
    assert result["outputs"]["r1"]["echo"] == "浇多少水"  # 模板插值 → 工具入参
    assert result["outputs"]["answer"] == result["final"]


def test_plugin_missing_required_param(growth_client, wf_tools):
    resp = growth_client.post(
        "/api/v1/plugins/register", json={"manifest": _manifest()}, headers=_headers()
    )
    assert resp.status_code == 201, resp.text
    growth_client.post(
        "/api/v1/plugins/wf.demo/review", json={"decision": "approve"}, headers=_headers()
    )
    resp = growth_client.post(
        "/api/v1/plugins/wf.demo/run", json={"params": {}}, headers=_headers()
    )
    assert resp.status_code == 400, resp.text
    assert "q" in resp.json()["error"]["details"]["missing"]


def test_plugin_reject_blocks_run(growth_client, wf_tools):
    resp = growth_client.post(
        "/api/v1/plugins/register",
        json={"manifest": _manifest("wf.reject")},
        headers=_headers(),
    )
    assert resp.status_code == 201, resp.text
    resp = growth_client.post(
        "/api/v1/plugins/wf.reject/review",
        json={"decision": "reject"},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "rejected"
    resp = growth_client.post(
        "/api/v1/plugins/wf.reject/run", json={"params": {"q": "x"}}, headers=_headers()
    )
    assert resp.status_code == 409, resp.text


def test_plugin_run_high_risk_tool_still_gated(growth_client, db_session, wf_tools):
    """双保险验证：即使插件已 enabled，高风险工具仍被既有逐次审批闸拦截。"""
    risky = _manifest(
        "wf.risky",
        params=[],
        steps=[{"kind": "tool", "tool": "wf.test.dispatch", "args": {"title": "t"}}],
    )
    resp = growth_client.post(
        "/api/v1/plugins/register", json={"manifest": risky}, headers=_headers()
    )
    assert resp.status_code == 201, resp.text
    growth_client.post(
        "/api/v1/plugins/wf.risky/review", json={"decision": "approve"}, headers=_headers()
    )
    resp = growth_client.post("/api/v1/plugins/wf.risky/run", json={}, headers=_headers())
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "TOOL_RISK_BLOCKED"
    assert resp.json()["error"]["details"]["approval_id"]
    # 审批单已自动创建（pending），说明走的是既有审批流而非绕过
    from app.modules.tools.models import ToolApproval  # 局部导入避免顶层耦合

    pending = (
        db_session.query(ToolApproval).filter(ToolApproval.tool_code == "wf.test.dispatch").all()
    )
    assert len(pending) == 1
    assert pending[0].status == "pending"


# ================================================================ C2 suggest + 边界
def test_plugin_suggest_mining_idempotent(growth_client, db_session, wf_tools):
    # 3 次重复 echo→notify 序列（≥3 次、长度 2）+ 1 次噪声运行
    for i in range(3):
        _seed_run(
            db_session,
            run_id=f"seq-{i}",
            tools=("wf.test.echo", "wf.test.notify"),
            question="巡检",
        )
    _seed_run(db_session, run_id="noise-1", tools=("wf.test.echo",), question="随便")

    resp = growth_client.post("/api/v1/plugins/suggest", headers=_headers())
    assert resp.status_code == 201, resp.text
    body = resp.json()["data"]
    assert body["created"] == 1
    draft = body["plugins"][0]
    assert draft["status"] == "pending_review"  # 只产草稿，绝不自动启用
    assert draft["created_from"] == "auto"
    assert draft["plugin_id"].startswith("auto-wf-")

    # 幂等：同 pattern 再挖一次不重复建
    resp = growth_client.post("/api/v1/plugins/suggest", headers=_headers())
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["created"] == 0

    # manifest._auto 溯源结构：pattern / occurrences / source_runs sha12
    record = plugin_service._find_record(db_session, draft["plugin_id"], YARD_A)  # noqa: SLF001
    assert record is not None
    auto = record.manifest_json["_auto"]
    assert auto["pattern"] == ["wf.test.echo", "wf.test.notify"]
    assert auto["occurrences"] == 3
    assert len(auto["source_runs"]) == 3
    assert all(len(x) == 12 for x in auto["source_runs"])  # sha12 摘要，不落 run_id 原文
    steps = record.manifest_json["steps"]
    assert [s["tool"] for s in steps] == ["wf.test.echo", "wf.test.notify"]
    assert all(s["kind"] == "tool" and s["save_as"] for s in steps)


def test_plugin_created_from_and_yard_boundary(growth_client, wf_tools):  # noqa: ARG001
    resp = growth_client.post(
        "/api/v1/plugins/register",
        json={"manifest": _manifest("wf.secret")},
        headers=_headers(),
    )
    assert resp.status_code == 201, resp.text

    # 详情含 manifest 正文（审核人看 steps）；小院 A 可见
    resp = growth_client.get("/api/v1/plugins/wf.secret", headers=_headers())
    assert resp.status_code == 200, resp.text
    detail = resp.json()["data"]
    assert detail["created_from"] == "manual"
    assert detail["manifest"]["steps"][0]["tool"] == "wf.test.echo"
    assert detail["checksum"]

    # 小院 B：列表看不到、详情/审核/运行一律 404/无权
    b = _headers("u-b", yard=YARD_B)
    listing = growth_client.get("/api/v1/plugins", headers=b).json()["data"]
    assert "wf.secret" not in {p["plugin_id"] for p in listing}
    assert growth_client.get("/api/v1/plugins/wf.secret", headers=b).status_code == 404
    assert (
        growth_client.post(
            "/api/v1/plugins/wf.secret/review", json={"decision": "approve"}, headers=b
        ).status_code
        == 404
    )
    assert (
        growth_client.post("/api/v1/plugins/wf.secret/run", json={}, headers=b).status_code == 404
    )
    # 小院 A 列表可见且 created_from 语义正确
    listing = growth_client.get("/api/v1/plugins", headers=_headers()).json()["data"]
    mine = {p["plugin_id"]: p for p in listing}
    assert mine["wf.secret"]["created_from"] == "manual"


def test_plugin_suggest_requires_admin(growth_client):
    resp = growth_client.post("/api/v1/plugins/suggest", headers=_headers("u-2", roles=("member",)))
    assert resp.status_code == 403, resp.text
