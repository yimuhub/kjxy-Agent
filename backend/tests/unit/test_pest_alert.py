"""虫情预警单元测试（v3.76 真实实现）。

覆盖：输入 schema / 真 LLM 结构化 / 模型不可用诚实降级 / 规则升级 /
agent 注册与运行。LLM 用 monkeypatch 桩（与 test_qa_agent 同构）。
"""

# import 触发 agent 自注册（含配置式定义注册）
import asyncio

import app.modules.pest_alert  # noqa: F401
import pytest
from app.modules.agents import get_global_registry
from app.modules.agents.definitions import get_definition
from app.modules.agents.schemas import AgentContext
from app.modules.farm.models import FarmAlert
from app.modules.pest_alert import service as pest_service
from app.modules.pest_alert.schemas import PestAlertInput, PestAlertOutput
from app.modules.pest_alert.service import run_pest_alert
from pydantic import ValidationError

LLM_JSON = (
    '{"level":"watch","summary":"稻飞虱风险上升，需到田核实",'
    '"risks":["近 7 天虫情告警 1 条"],"recommendations":["到田查虫口密度"]}'
)


def _make_context(db_session) -> AgentContext:
    return AgentContext(
        task_id="task-test",
        yard_id="yard-test",
        actor_id="user-test",
        request_id="req-test",
        allowed_tools=["knowledge.search"],
        deadline_seconds=60,
        metadata={"db": db_session},
    )


@pytest.fixture()
def fake_llm(monkeypatch):
    """可用模型：直接返回合法 JSON。"""

    def _fake(db, req):
        class _R:
            content = LLM_JSON

        return _R()

    monkeypatch.setattr(pest_service, "complete_with_logging", _fake)
    return _fake


def test_input_schema_validation():
    # query 现在可选（为空则基于本院数据做通用预警）
    parsed = PestAlertInput()
    assert parsed.query == ""
    parsed2 = PestAlertInput(query="稻飞虱", crop="水稻")
    assert parsed2.crop == "水稻"
    with pytest.raises(ValidationError):
        PestAlertInput(top_k=99)  # 超出检索上限应校验失败


def test_service_rule_fallback_without_llm(db_session, monkeypatch):
    """模型不可用 → 诚实降级为规则摘要（非 AI 生成），绝不假装智能预警。"""
    monkeypatch.setattr(pest_service, "complete_with_logging", None)
    result = asyncio.run(
        run_pest_alert(db_session, "yard-test", "user-test", {"query": "稻飞虱"})
    )
    assert isinstance(result, dict)
    assert result["level"] in ("none", "watch", "warning", "critical")
    assert "非 AI 生成" in result["answer"]
    assert any("模型服务暂不可用" in w for w in result["warnings"])
    PestAlertOutput(**result)  # 输出必须过 schema 校验


def test_service_with_real_llm_and_alerts(db_session, fake_llm):
    """有告警 + 有模型 → 结构化预警，evidence 带本院告警。"""
    db_session.add(
        FarmAlert(
            yard_id="yard-test",
            title="稻飞虱虫量上升预警",
            detail="百丛虫量超 1500 头",
            severity="critical",
            source="manual",
        )
    )
    db_session.commit()
    result = asyncio.run(
        run_pest_alert(
            db_session, "yard-test", "user-test", {"query": "稻飞虱", "crop": "水稻"}
        )
    )
    assert result["level"] == "watch"
    assert "稻飞虱" in result["summary"]
    kinds = {e["kind"] for e in result["evidence"]}
    assert "alert" in kinds  # 本院告警进入证据
    PestAlertOutput(**result)


def test_rule_level_escalation(db_session, monkeypatch):
    """模型不可用 + critical 告警 → 规则等级至少 warning。"""
    monkeypatch.setattr(pest_service, "complete_with_logging", None)
    db_session.add(
        FarmAlert(
            yard_id="yard-test",
            title="二化螟集中危害",
            detail="枯心团成片",
            severity="critical",
            source="manual",
        )
    )
    db_session.commit()
    result = asyncio.run(run_pest_alert(db_session, "yard-test", "user-test", {}))
    assert result["level"] == "warning"
    assert any("严重告警" in r for r in result["risks"])


@pytest.mark.asyncio
async def test_agent_registered_and_runs(db_session, fake_llm):
    assert get_definition("pest_alert") is not None
    assert get_definition("pest_alert")["version"] == "1.1.0"
    agent = get_global_registry().get("pest_alert")
    assert agent.code == "pest_alert"
    result = await agent.run({"query": "稻飞虱", "crop": "水稻"}, _make_context(db_session))
    assert result.status == "succeeded"
    assert result.output["level"] in ("none", "watch", "warning", "critical")
    assert "非 AI" not in result.output["answer"]  # 有模型时不应出现降级文案
