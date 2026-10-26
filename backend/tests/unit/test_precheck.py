"""双层验收测试：机器预检纯函数断言 + LLM 深检的裁判兜底口径。

覆盖（对应借鉴 agency-orchestrator 的核心防幻觉设计）：
- precheck_candidate：通过/长度/来源/乱码/模板/敏感串各断言，且失败必引用原话；
- deep_precheck：全过、无引用指控改判、有引用指控保留、裁判异常降级放行、
  自造标准丢弃与未答标准补全。
"""

import json
from types import SimpleNamespace

import pytest
from app.modules.evolution.precheck import (
    ACCEPTANCE_CRITERIA,
    deep_precheck,
    precheck_candidate,
)
from app.modules.llm.schemas import LLMRequest, LLMResponse

GOOD_BODY = (
    "水稻分蘖期每亩追施尿素五公斤并保持浅水层管理，"
    "促进低位分蘖萌发。适用于江苏中部单季稻，"
    "拔节前的施肥量需结合苗情酌减，避免无效分蘖过多。"
    "本条来自试点小院 2026 年专家复核记录，"
    "与小院既有资料《水稻分蘖期管理技术要点》口径一致。"
)


def _candidate(content: str = GOOD_BODY, source: str = "https://example.com/doc/1", **kw):
    return SimpleNamespace(title="水稻分蘖期追肥要点", content=content, source=source, **kw)


class TestPrecheckCandidate:
    def test_good_candidate_passes(self):
        result = precheck_candidate(_candidate())
        assert result["pass"] is True
        assert result["issues"] == []

    def test_too_short_fails_with_quote(self):
        result = precheck_candidate(_candidate(content="太短了。"))
        assert result["pass"] is False
        issue = next(i for i in result["issues"] if "长度" in i["criterion"])
        # 失败必须引用命中的原话片段（≤40 字），禁止空口指控
        assert "太短了" in issue["why"]

    def test_missing_source_fails(self):
        result = precheck_candidate(_candidate(source=""))
        assert result["pass"] is False
        assert any("来源" in i["criterion"] for i in result["issues"])

    def test_bad_url_fails(self):
        result = precheck_candidate(_candidate(source="https://"))
        assert any("来源" in i["criterion"] for i in result["issues"])

    def test_gibberish_fails(self):
        result = precheck_candidate(_candidate(content="\ufffd" * 80 + GOOD_BODY[:20]))
        assert result["pass"] is False
        assert any("乱码" in i["criterion"] or "可读" in i["criterion"] for i in result["issues"])

    def test_placeholder_template_fails(self):
        result = precheck_candidate(
            _candidate(content=GOOD_BODY[:30] + "XXXXXX待补充" + GOOD_BODY[30:])
        )
        assert any("模板" in i["criterion"] or "占位" in i["criterion"] for i in result["issues"])

    def test_sensitive_phone_fails_with_quote(self):
        result = precheck_candidate(_candidate(content=GOOD_BODY + "联系人手机13812345678请记录"))
        assert result["pass"] is False
        issue = next(i for i in result["issues"] if "敏感" in i["criterion"])
        assert "13812345678" in issue["why"]


class _FakeLLM:
    def __init__(self, content: str | None = None, error: Exception | None = None):
        self._content = content
        self._error = error

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if self._error is not None:
            raise self._error
        return LLMResponse(content=self._content or "", model="fake", latency_ms=1)


def _verdict(criterion: str, passed: bool, why: str) -> str:
    return json.dumps(
        [
            {
                "criterion": c,
                "pass": passed if c == criterion else True,
                "why": why if c == criterion else "满足",
            }
            for c in ACCEPTANCE_CRITERIA
        ],
        ensure_ascii=False,
    )


@pytest.mark.asyncio
class TestDeepPrecheck:
    async def test_all_pass(self):
        raw = json.dumps(
            [{"criterion": c, "pass": True, "why": "满足"} for c in ACCEPTANCE_CRITERIA],
            ensure_ascii=False,
        )
        result = await deep_precheck(_candidate(), _FakeLLM(raw))
        assert result["pass"] is True and result["error"] is None
        assert len(result["results"]) == len(ACCEPTANCE_CRITERIA)

    async def test_uncited_failure_flipped_to_pass(self):
        # 裁判判“不满足”但拿不出候选原话 → 视为不可靠判定，兜底改判满足
        raw = _verdict(ACCEPTANCE_CRITERIA[0], False, "正文没有写清楚区域范围")
        result = await deep_precheck(_candidate(), _FakeLLM(raw))
        item = next(i for i in result["results"] if i["criterion"] == ACCEPTANCE_CRITERIA[0])
        assert item["pass"] is True
        assert "兜底改判" in item["why"]
        assert result["pass"] is True

    async def test_cited_failure_kept(self):
        # 判“不满足”且引用了 ≥8 字连续原话 → 指控保留，整体不通过
        quote = "追施尿素五公斤并保持浅水层管理"
        raw = _verdict(ACCEPTANCE_CRITERIA[1], False, f"候选写「{quote}」但缺少减量标注")
        result = await deep_precheck(_candidate(), _FakeLLM(raw))
        item = next(i for i in result["results"] if i["criterion"] == ACCEPTANCE_CRITERIA[1])
        assert item["pass"] is False
        assert result["pass"] is False

    async def test_invented_criterion_dropped_and_missing_appended(self):
        raw = json.dumps(
            [{"criterion": "必须配图", "pass": False, "why": "没图"}],
            ensure_ascii=False,
        )
        result = await deep_precheck(_candidate(), _FakeLLM(raw))
        criteria = {i["criterion"] for i in result["results"]}
        assert "必须配图" not in criteria  # 裁判自造标准丢弃
        assert criteria == set(ACCEPTANCE_CRITERIA)  # 未答标准按通过补全

    async def test_llm_error_degrades_to_pass(self):
        result = await deep_precheck(_candidate(), _FakeLLM(error=RuntimeError("boom")))
        assert result["pass"] is True
        assert result["error"] is not None and "深检未生效" in result["error"]

    async def test_unparseable_output_degrades_to_pass(self):
        result = await deep_precheck(_candidate(), _FakeLLM("我不确定，非 JSON"))
        assert result["pass"] is True
        assert result["error"] is not None
