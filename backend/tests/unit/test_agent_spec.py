"""AgentSpec 固定框架校验单测（v3.5）：八类拒绝路径 + 兜底模板恒合法。"""

import pytest
from app.core.errors import AppError
from app.modules.agents import agent_spec


def _valid_payload() -> dict:
    """一份合法规格（全部必填段齐全）。"""
    return {
        "agent_code": "gen_pest_helper",
        "name": "病虫害助手",
        "description": "为种植户提供病虫害识别与防治建议",
        "emoji": "🐛",
        "mission": "为小院种植户解决病虫害识别与防治决策问题",
        "audience": "小院种植户与技术员",
        "in_scope": ["识别常见病虫害", "给出防治方案", "提示用药安全"],
        "out_of_scope": ["不做最终诊断结论", "不替代专家签字"],
        "workflow": [
            {"action": "澄清症状与作物", "tool": None},
            {"action": "检索知识库", "tool": "knowledge.search"},
            {"action": "给出结论与依据", "tool": None},
        ],
        "guardrails": ["依据不足先追问", "用药建议必须给核实路径"],
        "output_format": "结论 / 依据 / 下一步",
        "persona_prompt": "你是病虫害防治助手，先澄清症状再依据知识库作答，不编造。",
        "task_prompt_template": "用户问题：{{question}}\n请检索知识库后作答。",
        "allowed_tools": ["knowledge.search"],
        "knowledge_scopes": ["yard"],
        "skill_needs": [{"need": "知识检索", "keywords": ["知识库", "检索"], "reason": "需要依据"}],
        "sources": [{"title": "农技手册", "url": "https://example.com/manual"}],
    }


TOOLS = {"knowledge.search": True, "sensor.query": True}


def _validate(payload: dict):
    return agent_spec.validate_agent_spec(payload, available_tools=TOOLS)


def test_valid_spec_passes_and_normalizes():
    spec = agent_spec.validate_agent_spec(_valid_payload(), available_tools=TOOLS)
    assert spec.agent_code == "gen_pest_helper"
    assert spec.workflow[1].tool == "knowledge.search"
    assert spec.skill_needs[0].need == "知识检索"


def test_rejects_non_gen_prefix():
    payload = _valid_payload() | {"agent_code": "pest_helper"}
    with pytest.raises(AppError) as e:
        _validate(payload)
    assert e.value.http_status == 422
    assert "gen_" in e.value.message


def test_rejects_unknown_fields():
    payload = _valid_payload() | {"risk_level": "low"}
    with pytest.raises(AppError) as e:
        _validate(payload)
    assert e.value.http_status == 422


def test_rejects_unregistered_tool_in_allowed_tools():
    payload = _valid_payload() | {"allowed_tools": ["knowledge.search", "shell.exec"]}
    with pytest.raises(AppError) as e:
        _validate(payload)
    assert "shell.exec" in e.value.message
    assert e.value.details.get("unknown_tools") == ["shell.exec"]


def test_rejects_unregistered_tool_in_workflow():
    payload = _valid_payload()
    payload["workflow"][1]["tool"] = "http.call"
    with pytest.raises(AppError) as e:
        _validate(payload)
    assert "http.call" in e.value.message


def test_rejects_workflow_out_of_bounds():
    two_steps = [
        {"action": "澄清问题", "tool": None},
        {"action": "给出建议", "tool": None},
    ]
    with pytest.raises(AppError):
        _validate(_valid_payload() | {"workflow": two_steps})
    seven = two_steps * 3 + [{"action": "收尾", "tool": None}]
    with pytest.raises(AppError):
        _validate(_valid_payload() | {"workflow": seven})


def test_rejects_scope_bounds_and_bad_scopes():
    with pytest.raises(AppError):
        _validate(_valid_payload() | {"in_scope": ["只有一条"]})
    with pytest.raises(AppError):
        _validate(_valid_payload() | {"out_of_scope": ["只有一条"]})
    with pytest.raises(AppError):
        _validate(_valid_payload() | {"knowledge_scopes": ["internet"]})


def test_rejects_template_without_question_placeholder():
    payload = _valid_payload() | {"task_prompt_template": "没有占位符的模板"}
    with pytest.raises(AppError) as e:
        _validate(payload)
    assert "{{question}}" in e.value.message


def test_rejects_skill_needs_without_keywords():
    payload = _valid_payload()
    payload["skill_needs"] = [{"need": "知识检索", "keywords": [], "reason": ""}]
    with pytest.raises(AppError):
        _validate(payload)


def test_sources_sanitized_and_capped():
    payload = _valid_payload()
    payload["sources"] = [
        {"title": f"s{i}", "url": f"https://example.com/{i}"} for i in range(12)
    ] + [{"title": "bad", "url": "ftp://x"}]
    spec = _validate(payload)
    assert len(spec.sources) == 8
    assert all(s["url"].startswith("https://") for s in spec.sources)


def test_fallback_spec_always_valid_with_and_without_knowledge_tool():
    spec = agent_spec.fallback_spec("帮我做个病虫害防治助手", available_tools=TOOLS)
    assert spec.agent_code.startswith("gen_")
    assert spec.workflow[1].tool == "knowledge.search"
    offline = agent_spec.fallback_spec("帮我做个病虫害防治助手", available_tools={})
    assert all(step.tool is None for step in offline.workflow)


def test_blueprint_payload_shape():
    spec = agent_spec.validate_agent_spec(_valid_payload(), available_tools=TOOLS)
    bp = agent_spec.blueprint_payload(
        spec, sources=[{"title": "x", "url": "https://a"}], offline=False, model="m", note=""
    )
    assert bp["framework"] == "agent-framework/v3.5"
    sections = ("mission", "audience", "in_scope", "out_of_scope", "workflow", "guardrails")
    for key in (*sections, "output_format"):
        assert key in bp
    assert bp["offline"] is False and bp["model"] == "m"


def test_build_spec_prompt_mentions_constraints():
    system, user = agent_spec.build_spec_prompt("做个病虫害助手", TOOLS, [], "（无技能）")
    assert "gen_" in system and "{{question}}" in system and "knowledge.search" in system
    assert "不得编造链接" in system and "做个病虫害助手" in user
