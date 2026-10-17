"""高风险红线回归（S4）：高危词表扩展（只扩不缩）与新旧资料数值冲突检测。

通过 qa_agent 的测试注入通道（_tool_registry_override）提供检索结果，
并替换 _generate_answer 为确定性文本，不依赖真实 LLM。
"""

import asyncio

import app.modules.agriculture.qa_agent as qa
import pytest


class _StubRegistry:
    """最小工具注册表替身：knowledge.search 固定返回预置 hits。"""

    def __init__(self, hits):
        self._hits = hits

    def get(self, name: str):
        if name != "knowledge.search":
            return None

        def _tool(payload, context=None):
            return {"hits": self._hits}

        return _tool


def _hit(doc_id: str, chunk_id: str, source: str, quote: str, title: str = "资料") -> dict:
    return {
        "document_id": doc_id,
        "chunk_id": chunk_id,
        "title": title,
        "source": source,
        "page_number": None,
        "quote": quote,
    }


def _run_agent(monkeypatch, question: str, hits: list[dict], answer: str = "按资料执行即可。"):
    monkeypatch.setattr(qa, "_tool_registry_override", _StubRegistry(hits))

    async def _fake_generate(question_, hits_, **kwargs):
        return answer

    monkeypatch.setattr(qa, "_generate_answer", _fake_generate)
    agent = qa.AgricultureQAAgent()
    ctx = qa.QAAgentContext(tenant_id="t-risk", user_id="u", roles=["operator"], request_id="r")
    return asyncio.run(agent.run({"question": question, "top_k": 5}, ctx))


# ---------------------------------------------------------------- 高危词表：只扩不缩
LEGACY_KEYWORDS = ("农药", "剂量", "施用", "农残", "防疫", "兽药")
NEW_KEYWORDS = ("种衣剂", "拌种", "药种比", "稀释倍数", "施药", "用药量", "安全间隔期", "农药残留")


def test_keyword_table_superset_of_legacy():
    """旧词表必须是新词表的子集——只扩不缩。"""
    for kw in LEGACY_KEYWORDS:
        assert kw in qa.HIGH_RISK_KEYWORDS
    for kw in NEW_KEYWORDS:
        assert kw in qa.HIGH_RISK_KEYWORDS


@pytest.mark.parametrize("kw", LEGACY_KEYWORDS + NEW_KEYWORDS)
def test_high_risk_keyword_triggers_review(monkeypatch, kw):
    """旧词与新词命中问题时同样触发风险提示并强制转专家复核。"""
    hits = [_hit("d1", "c1", "upload", "分蘖期保持浅水勤灌。")]
    result = _run_agent(monkeypatch, f"关于{kw}应该怎么做？", hits)
    output = result.output
    assert output["needs_expert_review"] is True
    assert qa.RISK_WARNING in output["warnings"]


def test_low_risk_question_no_risk_warning(monkeypatch):
    hits = [_hit("d1", "c1", "upload", "分蘖期保持浅水勤灌，忌长期深水淹灌。")]
    result = _run_agent(monkeypatch, "水稻分蘖期怎么浇水？", hits)
    output = result.output
    assert qa.RISK_WARNING not in output["warnings"]
    assert output["needs_expert_review"] is False


# ---------------------------------------------------------------- 新旧资料数值冲突
CONFLICT_PAIR = [
    _hit("d-old", "c-old", "upload", "旧资料：水稻拌种的药种比为 1:30，沿用多年。", title="旧资料"),
    _hit(
        "d-exp",
        "c-exp",
        "expert_correction",
        "专家更正：拌种的药种比是多少？正确为 1:50，1:30 有误。",
        title="专家更正",
    ),
]


def test_conflict_values_force_expert_review(monkeypatch):
    """expert_correction 与 upload 并存且数值不一致 → 转专家 + 列值列来源。"""
    result = _run_agent(
        monkeypatch, "这个田到底怎么拌种？", CONFLICT_PAIR, answer="建议按 1:50 配制。"
    )
    output = result.output
    assert output["needs_expert_review"] is True
    conflict_warnings = [w for w in output["warnings"] if "检测到新旧资料" in w]
    assert conflict_warnings, f"应追加冲突警示，实际 warnings={output['warnings']}"
    msg = conflict_warnings[0]
    assert "1:50" in msg and "1:30" in msg
    assert "expert_correction" in msg and "upload" in msg
    assert "已转专家复核" in msg


def test_conflict_unit_helper_requires_both_sources():
    """无 expert_correction、或无 upload/tenant_material、或数值一致 → 不判冲突。"""
    # 只有一类来源
    only_upload = [_hit("d1", "c1", "upload", "药种比 1:30")]
    assert qa._detect_conflicting_values("按 1:50", only_upload) is None
    # 两类来源但数值一致
    same_value = [
        _hit("d1", "c1", "upload", "药种比 1:30"),
        _hit("d2", "c2", "seed", "药种比 1:30"),
        _hit("d3", "c3", "expert_correction", "确认药种比 1:30"),
    ]
    assert qa._detect_conflicting_values("按 1:30", same_value) is None
    # 两类来源、无任何数值
    no_values = [
        _hit("d1", "c1", "upload", "按专家意见执行"),
        _hit("d2", "c2", "expert_correction", "维持原方案"),
    ]
    assert qa._detect_conflicting_values("维持原方案", no_values) is None


def test_conflict_with_tenant_material_source():
    """tenant_material 也属于"旧资料"侧，参与冲突判定。"""
    pair = [
        _hit("d1", "c1", "tenant_material", "亩用 100毫升"),
        _hit("d2", "c2", "expert_correction", "更正：亩用 50毫升"),
    ]
    msg = qa._detect_conflicting_values("酌情处理", pair)
    assert msg is not None and "50毫升" in msg and "100毫升" in msg


def test_conflict_independent_of_high_risk_keyword(monkeypatch):
    """问题不含高危词也应因数值冲突转专家（红线独立于关键词触发）。"""
    result = _run_agent(monkeypatch, "这个配比到底按哪个来？", CONFLICT_PAIR)
    output = result.output
    assert output["needs_expert_review"] is True
    assert qa.RISK_WARNING not in output["warnings"]
    assert any("检测到新旧资料" in w for w in output["warnings"])
