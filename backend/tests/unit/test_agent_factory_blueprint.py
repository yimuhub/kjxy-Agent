"""Agent 工厂蓝图单元测试：解析（正常/围栏/坏 JSON）、启发式兜底、撞码防护。

口径与集成链路保持一致：字段齐全、工具白名单硬过滤（只认注册表只读工具）、
agent_code 命中 ^[a-z][a-z0-9_]*$、assess_risk 对工厂默认配置判 low（自批前提）。
"""

import json
import re

import pytest
from app.modules.agents import declarative
from app.modules.agents.declarative import DeclarativeAgentCreate, assess_risk
from app.modules.agents.factory_blueprint import (
    DEFAULT_BLUEPRINT_TOOL,
    DEFAULT_KNOWLEDGE_SCOPES,
    finalize_codes,
    heuristic_blueprint,
    parse_blueprint,
)
from app.modules.tenants.repository import create_tenant

CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _role(i: int, **over) -> dict:
    base = {
        "agent_code": f"role_{i}",
        "name": f"角色{i}",
        "emoji": "🌱",
        "description": "职责描述",
        "persona_prompt": f"你是角色{i}，务实回答。",
        "task_prompt_template": f"请回答：{{{{question}}}}（角色{i}）",
        "allowed_tools": ["knowledge.search"],
        "knowledge_scopes": ["tenant"],
        "suggested_questions": [f"问题{i}-{j}？" for j in range(5)],
    }
    base.update(over)
    return base


def _blueprint_text(n: int = 4) -> str:
    return json.dumps({"agents": [_role(i) for i in range(1, n + 1)]}, ensure_ascii=False)


def test_parse_blueprint_plain_json_ok():
    items = parse_blueprint(_blueprint_text(4), known_tools=["knowledge.search"])
    assert len(items) == 4
    first = items[0]
    for field in (
        "agent_code",
        "name",
        "emoji",
        "description",
        "persona_prompt",
        "task_prompt_template",
        "allowed_tools",
        "knowledge_scopes",
        "suggested_questions",
    ):
        assert first[field] is not None
    assert "{{question}}" in first["task_prompt_template"]


def test_parse_blueprint_tolerates_code_fence_and_prose():
    raw = "好的，蓝图如下：\n```json\n" + _blueprint_text(3) + "\n```\n以上。"
    items = parse_blueprint(raw, known_tools=["knowledge.search"])
    assert len(items) == 3


def test_parse_blueprint_bad_json_raises():
    with pytest.raises(ValueError):
        parse_blueprint("对不起，我无法生成蓝图。", known_tools=[])
    # 结构缺失（没有 agents 数组）同样必须抛错 → 触发启发式兜底
    with pytest.raises(ValueError):
        parse_blueprint('{"plans": []}', known_tools=[])
    # 有效角色不足下限（<3）也必须抛错
    with pytest.raises(ValueError):
        parse_blueprint(_blueprint_text(1), known_tools=["knowledge.search"])


def test_parse_blueprint_enforces_defaults_and_tool_filter():
    raw = json.dumps(
        {
            "agents": [
                _role(1, allowed_tools=["hack.tool"], knowledge_scopes=[], suggested_questions=[]),
                *[_role(i) for i in range(2, 5)],
            ]
        },
        ensure_ascii=False,
    )
    items = parse_blueprint(raw, known_tools=["knowledge.search"])
    assert len(items) == 4
    first = items[0]
    # 白名单硬过滤：未知工具被丢弃后回落默认只读工具
    assert first["allowed_tools"] == [DEFAULT_BLUEPRINT_TOOL]
    # 缺省补齐：知识范围与占位符
    assert first["knowledge_scopes"] == DEFAULT_KNOWLEDGE_SCOPES
    assert first["suggested_questions"] == []


def test_parse_blueprint_backfills_question_placeholder():
    raw = json.dumps(
        {"agents": [_role(i, task_prompt_template="请回答问题") for i in range(1, 4)]},
        ensure_ascii=False,
    )
    items = parse_blueprint(raw, known_tools=["knowledge.search"])
    assert all("{{question}}" in i["task_prompt_template"] for i in items)


def test_heuristic_blueprint_fields_complete_and_deterministic():
    items = heuristic_blueprint("果园农机服务，覆盖修剪与采摘环节", focus="侧重农机服务")
    assert len(items) == 4
    codes = []
    for item in items:
        for field in (
            "agent_code",
            "name",
            "emoji",
            "description",
            "persona_prompt",
            "task_prompt_template",
            "allowed_tools",
            "knowledge_scopes",
            "suggested_questions",
        ):
            assert item[field] is not None and item[field] != [], field
        assert CODE_RE.match(item["agent_code"]), item["agent_code"]
        assert "{{question}}" in item["task_prompt_template"]
        assert item["allowed_tools"] == [DEFAULT_BLUEPRINT_TOOL]
        assert item["knowledge_scopes"] == DEFAULT_KNOWLEDGE_SCOPES
        assert len(item["suggested_questions"]) == 5
        # persona 按行业描述与侧重方向定制（兜底不是通用模板）
        assert "果园农机服务" in item["persona_prompt"]
        assert "侧重农机服务" in item["persona_prompt"]
        codes.append(item["agent_code"])
    assert len(set(codes)) == 4
    # 确定性：同输入两次产物一致（可测试、可复现）
    assert heuristic_blueprint("果园农机服务，覆盖修剪与采摘环节", "侧重农机服务") == items


def test_finalize_codes_unique_safe_and_run_scoped():
    items = heuristic_blueprint("industrial drones")
    finalize_codes(items, "11112222-3333-4444-5555-666677778888")
    codes = [i["agent_code"] for i in items]
    assert len(set(codes)) == 4
    for code in codes:
        assert CODE_RE.match(code), code
        assert len(code) <= 100
        assert code.endswith("11112222")  # run 短 id 后缀，跨 run 防撞码
    # 与平台内置 Agent 编码天然错开（后缀使 factory 产物永不撞内置）
    from app.modules.agents.definitions import AGENT_DEFINITIONS

    builtin = {d["code"] for d in AGENT_DEFINITIONS}
    assert not (set(codes) & builtin)


def test_assess_risk_low_for_factory_defaults():
    # 工厂默认产物（只读工具 + 租户知识范围）必须是低风险：自批前提
    assert assess_risk([DEFAULT_BLUEPRINT_TOOL], DEFAULT_KNOWLEDGE_SCOPES) == "low"
    # 冒烟：蓝图默认工具全部命中只读白名单 → 整组判 low
    for item in heuristic_blueprint("测试行业"):
        assert assess_risk(item["allowed_tools"], item["knowledge_scopes"]) == "low", item[
            "agent_code"
        ]


def test_create_draft_from_blueprint_item_is_draft_factory_origin(db_session):
    """assess_risk 集成冒烟：蓝图角色 → create_agent → draft/factory/low。"""
    tenant = create_tenant(db_session, name="工厂蓝图租户")
    db_session.commit()
    item = heuristic_blueprint("水稻育秧服务")[0]
    finalize_codes([item], "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    req = DeclarativeAgentCreate(
        agent_code=item["agent_code"],
        name=item["name"],
        description=item["description"],
        emoji=item["emoji"],
        persona_prompt=item["persona_prompt"],
        task_prompt_template=item["task_prompt_template"],
        allowed_tools=item["allowed_tools"],
        knowledge_scopes=item["knowledge_scopes"],
        created_from="factory",
    )
    agent = declarative.create_agent(
        db_session,
        tenant_id=tenant.id,
        actor_id="u-factory",
        req=req,
        request_id="req-unit-factory",
    )
    db_session.commit()
    assert agent.status == "draft"
    assert agent.created_from == "factory"
    assert agent.risk_level == declarative.RISK_LOW
