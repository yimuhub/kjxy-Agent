"""GET /agents 的 skills 字段：存在且与 ToolRegistry 实际注册一致。

文档化/可视化专用测试：只校验 GET /agents 返回给每个 Agent 附加了
``skills`` 精简列表，不改变任何 allowed_tools 配置与注册逻辑。
"""

from app.core.security import create_access_token
from app.modules.agents.definitions import AGENT_DEFINITIONS
from app.modules.tools.registry import tool_registry


def _auth():
    token = create_access_token(user_id="u1", tenant_id="t1", roles=["tenant_admin"])
    return {"Authorization": f"Bearer {token}"}


def test_list_agents_has_skills_field(client):
    resp = client.get("/api/v1/agents", headers=_auth())
    assert resp.status_code == 200
    agents = resp.json()["data"]["agents"]
    assert agents, "agents 列表不应为空"
    for a in agents:
        assert "skills" in a, f"Agent {a.get('code')} 缺少 skills 字段"
        assert isinstance(a["skills"], list)
        for s in a["skills"]:
            assert {"code", "name", "description", "risk_level"} <= set(s.keys())


def test_skills_match_definitions_and_registry(client):
    """skills 必须与 definitions.allowed_tools + ToolRegistry 实际注册一一对应。"""
    resp = client.get("/api/v1/agents", headers=_auth())
    assert resp.status_code == 200
    by_code = {a["code"]: a for a in resp.json()["data"]["agents"]}

    for definition in AGENT_DEFINITIONS:
        if not definition.get("enabled", True):
            continue
        code = definition["code"]
        assert code in by_code, f"GET /agents 缺失 Agent：{code}"
        skills = by_code[code]["skills"]
        skill_codes = [s["code"] for s in skills]
        for allowed in definition.get("allowed_tools") or []:
            tool = tool_registry.get(allowed)  # 实际注册为准，不存在的 tool 会抛错
            assert allowed in skill_codes, f"{code} 的 skill {allowed} 在返回中缺失"
            summary = next(s for s in skills if s["code"] == allowed)
            assert summary["name"] == tool.name
            assert summary["description"] == tool.description
            assert summary["risk_level"] == tool.risk_level
        # skills 数量恰好等于 allowed_tools 中注册表存在的部分
        assert len(skills) == len(
            [t for t in (definition.get("allowed_tools") or []) if t in skill_codes]
        )


def test_agents_without_tools_have_empty_skills(client):
    """allowed_tools 为空的 Agent（如 research_assistant / data_analyst）skills 为空数组。"""
    resp = client.get("/api/v1/agents", headers=_auth())
    assert resp.status_code == 200
    by_code = {a["code"]: a for a in resp.json()["data"]["agents"]}
    empty_expected = [
        d["code"]
        for d in AGENT_DEFINITIONS
        if d.get("enabled", True) and not d.get("allowed_tools")
    ]
    assert empty_expected, "前置假设：至少有一个 Agent 的 allowed_tools 为空"
    for code in empty_expected:
        assert by_code[code]["skills"] == [], f"{code} 的 skills 应为空"


def test_existing_fields_not_broken(client):
    """加 skills 字段不破坏现有字段（向后兼容）。"""
    resp = client.get("/api/v1/agents", headers=_auth())
    assert resp.status_code == 200
    for a in resp.json()["data"]["agents"]:
        for field in ("code", "name", "version", "description", "risk_level", "max_steps"):
            assert field in a, f"Agent {a.get('code')} 缺少既有字段 {field}"
