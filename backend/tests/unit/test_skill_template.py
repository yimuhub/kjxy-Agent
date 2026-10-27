"""技能模板契约：严格校验、工具对照与风险升级、Markdown 渲染往返。"""

from __future__ import annotations

import pytest
from app.core.errors import AppError
from app.modules.market.skill_template import (
    SkillSpec,
    dump_markdown,
    validate_skill_spec,
)
from app.modules.market.skilldoc import parse_frontmatter

# 工具注册表对照：code → 是否只读
TOOLS: dict[str, bool] = {
    "knowledge.search": True,
    "sensor.query": True,
    "workorder.dispatch": False,  # 非只读（external_side_effect 演示工具）
}


def _raw_spec(**overrides) -> dict:
    """合法样例（与 contracts/skill-template.yaml 示例同构）。"""
    data = {
        "code": "pest_scout",
        "name": "麦田病虫害侦察助手",
        "version": "1.0.0",
        "category": "pest_control",
        "summary": "识别麦田常见病虫害并给出处置建议。",
        "when_to_use": "用户描述麦田叶片异常、虫口密度或需要巡田建议时使用。",
        "inputs": [
            {"key": "plot_id", "label": "地块编号", "required": True, "type": "string"},
            {"key": "symptom", "label": "症状描述", "required": False, "type": "text"},
        ],
        "steps": [
            {"kind": "prompt", "title": "澄清症状", "text": "先确认作物生育期与受害部位。"},
            {
                "kind": "tool",
                "title": "检索知识库",
                "tool": "knowledge.search",
                "text": "检索本小院病虫害处置知识。",
            },
            {"kind": "llm", "title": "汇总结论", "text": "汇总证据给出处置建议。"},
        ],
        "allowed_tools": ["knowledge.search"],
        "knowledge_scopes": ["yard", "crop_public"],
        "prompt": "你是麦田病虫害侦察助手，依据知识库证据判断病虫害并给出处置建议。",
        "examples": [
            {"question": "麦叶发黄怎么办？", "answer_outline": "先排查纹枯病与黄矮病，再看根部。"}
        ],
        "eval_cases": [
            {"input": "麦田发现蚜虫聚集", "expect": "达防治指标才用药，给出用量与安全间隔期"},
            {"input": "叶片出现白粉", "expect": "判断白粉病并建议通风降湿与药剂防治"},
        ],
        "guardrails": {"risk_level": "low", "max_steps": 5, "require_citation": True},
        "output_format": "markdown",
        "sources": [{"title": "全国农技中心病虫害测报", "url": "https://www.natesc.org.cn/"}],
        "tags": ["pest", "wheat"],
        "license": "MIT",
    }
    data.update(overrides)
    return data


def test_valid_spec_passes():
    spec = validate_skill_spec(_raw_spec(), available_tools=TOOLS)
    assert isinstance(spec, SkillSpec)
    assert spec.code == "pest_scout"
    assert spec.guardrails.risk_level == "low"
    assert spec.requires_review is False
    assert spec.steps[1].tool == "knowledge.search"


def test_unknown_field_rejected():
    raw = _raw_spec(secret_backdoor="on")
    with pytest.raises(AppError) as err:
        validate_skill_spec(raw, available_tools=TOOLS)
    assert err.value.code == "VALIDATION_ERROR"
    assert err.value.http_status == 422
    assert "secret_backdoor" in err.value.message


@pytest.mark.parametrize("code", ["PestScout", "ab", "1abc", "pest-scout中", "x" * 62])
def test_invalid_code_rejected(code):
    with pytest.raises(AppError) as err:
        validate_skill_spec(_raw_spec(code=code), available_tools=TOOLS)
    assert err.value.code == "VALIDATION_ERROR"
    assert err.value.http_status == 422


@pytest.mark.parametrize(
    "code",
    ["builtin", "system", "admin", "builtin_helper", "system_tool", "admin_panel"],
)
def test_reserved_code_rejected(code):
    with pytest.raises(AppError) as err:
        validate_skill_spec(_raw_spec(code=code), available_tools=TOOLS)
    assert err.value.code == "VALIDATION_ERROR"
    assert "保留前缀" in err.value.message


def test_unregistered_tool_rejected():
    raw = _raw_spec(allowed_tools=["weather.query"])
    with pytest.raises(AppError) as err:
        validate_skill_spec(raw, available_tools=TOOLS)
    assert err.value.code == "VALIDATION_ERROR"
    assert err.value.http_status == 422
    assert err.value.message == "技能引用了未注册的工具：weather.query"


def test_unregistered_step_tool_rejected():
    raw = _raw_spec(
        steps=[{"kind": "tool", "tool": "market.scrape", "text": "抓取行情"}],
        allowed_tools=["knowledge.search"],
    )
    with pytest.raises(AppError) as err:
        validate_skill_spec(raw, available_tools=TOOLS)
    assert "技能引用了未注册的工具：market.scrape" in err.value.message


def test_step_tool_must_be_listed_in_allowed_tools():
    raw = _raw_spec(
        allowed_tools=[],  # 注册了但未授权给技能
        steps=[{"kind": "tool", "tool": "knowledge.search", "text": "检索"}],
    )
    with pytest.raises(AppError) as err:
        validate_skill_spec(raw, available_tools=TOOLS)
    assert "步骤工具未列入 allowed_tools：knowledge.search" in err.value.message


def test_write_tool_escalates_risk_and_requires_review():
    raw = _raw_spec(
        allowed_tools=["workorder.dispatch"],
        steps=[
            {"kind": "tool", "tool": "workorder.dispatch", "text": "下发处置工单"},
        ],
        guardrails={"risk_level": "medium", "max_steps": 5, "require_citation": True},
    )
    spec = validate_skill_spec(raw, available_tools=TOOLS)
    assert spec.guardrails.risk_level == "high"
    assert spec.requires_review is True


def test_eval_cases_below_minimum_rejected():
    raw = _raw_spec(eval_cases=[{"input": "q", "expect": "a"}])
    with pytest.raises(AppError) as err:
        validate_skill_spec(raw, available_tools=TOOLS)
    assert err.value.code == "VALIDATION_ERROR"
    assert err.value.http_status == 422


def test_steps_over_limit_rejected():
    steps = [{"kind": "prompt", "text": f"第 {i} 步"} for i in range(11)]
    with pytest.raises(AppError) as err:
        validate_skill_spec(_raw_spec(steps=steps), available_tools=TOOLS)
    assert "超过上限 10" in err.value.message

    # 调用方收紧上限：max_steps=3 时 4 步也拒绝
    with pytest.raises(AppError) as err:
        validate_skill_spec(_raw_spec(steps=steps[:4]), available_tools=TOOLS, max_steps=3)
    assert "超过上限 3" in err.value.message


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "ftp://example.com/doc.pdf", "file:///etc/passwd"]
)
def test_non_http_url_rejected(url):
    raw = _raw_spec(sources=[{"title": "外部资料", "url": url}])
    with pytest.raises(AppError) as err:
        validate_skill_spec(raw, available_tools=TOOLS)
    assert err.value.code == "VALIDATION_ERROR"
    assert err.value.http_status == 422


def test_sources_empty_with_citation_rejected():
    raw = _raw_spec(sources=[])
    with pytest.raises(AppError) as err:
        validate_skill_spec(raw, available_tools=TOOLS)
    assert "sources 为空时禁止声明 require_citation" in err.value.message

    # 显式关闭 require_citation 后允许无来源
    raw["guardrails"] = {"risk_level": "low", "max_steps": 5, "require_citation": False}
    spec = validate_skill_spec(raw, available_tools=TOOLS)
    assert spec.guardrails.require_citation is False


def test_invalid_semver_rejected():
    with pytest.raises(AppError) as err:
        validate_skill_spec(_raw_spec(version="v1"), available_tools=TOOLS)
    assert err.value.code == "VALIDATION_ERROR"


def test_dump_markdown_roundtrip():
    spec = validate_skill_spec(_raw_spec(), available_tools=TOOLS)
    text = dump_markdown(spec)
    meta, body = parse_frontmatter(text)
    # frontmatter 标量与 spec 一致（parse_frontmatter 一律返回字符串标量）
    assert meta["code"] == spec.code
    assert meta["name"] == spec.name
    assert meta["version"] == "1.0.0"
    assert meta["category"] == "pest_control"
    assert meta["output_format"] == "markdown"
    assert meta["license"] == "MIT"
    assert meta["summary"] == spec.summary
    assert [t.strip() for t in str(meta["tags"]).split(",")] == spec.tags
    assert meta["risk_level"] == "low"
    assert meta["max_steps"] == "5"
    assert meta["require_citation"] == "true"
    assert meta["requires_review"] == "false"
    # 正文分节：与 GitHub SKILL.md 惯例对齐（Purpose/Instructions/Tools 可被导入链路复用）
    assert "# 麦田病虫害侦察助手" in body
    assert "## Purpose" in body
    assert "## Instructions" in body
    assert "调用工具 `knowledge.search`" in body
    assert "## Allowed tools" in body
    assert spec.prompt in body
    assert "## Eval cases" in body
    assert "## Sources" in body
    assert "[全国农技中心病虫害测报](https://www.natesc.org.cn/)" in body


def test_dump_markdown_write_tool_marks_review():
    raw = _raw_spec(
        allowed_tools=["workorder.dispatch"],
        steps=[{"kind": "tool", "tool": "workorder.dispatch", "text": "下发处置工单"}],
    )
    spec = validate_skill_spec(raw, available_tools=TOOLS)
    text = dump_markdown(spec)
    meta, _ = parse_frontmatter(text)
    assert meta["risk_level"] == "high"
    assert meta["requires_review"] == "true"
