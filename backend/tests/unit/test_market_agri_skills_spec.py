"""内置农业技能补齐 SkillSpec 契约字段后的结构校验（v3.26 D-1.2）。

不追求与 SkillSpec 全量字段一致（steps 仍保留字符串形式以兼容既有注入链路），
校验的是「技能详情能展示、评测闸能读取」所需的字段完整性与形状。
"""

from __future__ import annotations

import pytest
from app.modules.market.agri_skills import _SPEC_EXTRAS, builtin_items

REGISTERED_TOOL_CODES = {"knowledge.search", "sensor.query", "web.search", "workorder.dispatch"}


@pytest.fixture(scope="module")
def items() -> list[dict]:
    return builtin_items()


def test_every_builtin_has_extras(items: list[dict]) -> None:
    """12 项都必须有配套的输入/用例/适用时机，不能漏配。"""
    codes = {item["code"] for item in items}
    assert codes == set(_SPEC_EXTRAS), "内置技能与 _SPEC_EXTRAS 覆盖不一致"


def test_contract_fields_present(items: list[dict]) -> None:
    for item in items:
        d = item["definition_json"]
        for field in (
            "code",
            "version",
            "summary",
            "when_to_use",
            "inputs",
            "eval_cases",
            "guardrails",
        ):
            assert d.get(field), (item["code"], field)
        assert d["code"] == item["code"]
        assert d["summary"] == d["description"]
        assert d["output_format"] == "markdown"
        assert d["knowledge_scopes"] == ["yard"]
        assert d["category"] == "agriculture"


def test_inputs_shape(items: list[dict]) -> None:
    for item in items:
        inputs = item["definition_json"]["inputs"]
        assert 1 <= len(inputs) <= 20
        keys = [i["key"] for i in inputs]
        assert len(set(keys)) == len(keys), f"{item['code']} 输入 key 重复"
        for i in inputs:
            assert i["type"] in {"string", "text", "number", "integer", "boolean", "enum"}
            assert isinstance(i["required"], bool)
            assert i["label"].strip()


def test_eval_cases_at_least_two(items: list[dict]) -> None:
    for item in items:
        cases = item["definition_json"]["eval_cases"]
        assert len(cases) >= 2, f"{item['code']} 评测用例不足 2 条"
        for case in cases:
            assert case["input"].strip() and case["expect"].strip()


def test_allowed_tools_are_registered(items: list[dict]) -> None:
    for item in items:
        tools = item["definition_json"]["allowed_tools"]
        assert set(tools) <= REGISTERED_TOOL_CODES, (item["code"], tools)


def test_guardrails_are_low_risk_and_cited(items: list[dict]) -> None:
    for item in items:
        g = item["definition_json"]["guardrails"]
        assert g["risk_level"] == "low"
        assert 1 <= g["max_steps"] <= 10
        assert g["require_citation"] is True
        assert item["definition_json"]["sources"], f"{item['code']} 声明引用但无 sources"


def test_legacy_fields_untouched(items: list[dict]) -> None:
    """既有链路依赖：steps 仍是字符串列表，prompt 仍是字符串。"""
    for item in items:
        d = item["definition_json"]
        assert isinstance(d["prompt"], str) and d["prompt"].strip()
        assert isinstance(d["steps"], list)
        assert all(isinstance(s, str) and s.strip() for s in d["steps"])
        assert d["upstream_skill"].startswith("farmfriend-labs/agent-skills-farming")
