"""虫情预警单元测试（SQLite 内存库）。

本文件由脚手架 app.modules.agents.scaffold 自动生成：3 个基础测试
（input schema 校验 / service 纯函数可跑 / agent 注册成功）。
"""

# import 触发 agent 自注册（含配置式定义注册）
import app.modules.pest_alert  # noqa: F401
import pytest
from app.modules.agents import get_global_registry
from app.modules.agents.definitions import get_definition
from app.modules.agents.schemas import AgentContext
from app.modules.pest_alert.schemas import PestAlertInput
from app.modules.pest_alert.service import run_pest_alert
from pydantic import ValidationError


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


def test_input_schema_validation():
    parsed = PestAlertInput(query="示例问题")
    assert parsed.query == "示例问题"
    with pytest.raises(ValidationError):
        PestAlertInput()  # query 必填，缺失应校验失败


def test_service_runs(db_session):
    result = run_pest_alert(db_session, "yard-test", "user-test", {"query": "示例问题"})
    assert isinstance(result, dict)
    assert "answer" in result
    assert "示例问题" in result["answer"]  # Fake LLM 回显问题摘要


@pytest.mark.asyncio
async def test_agent_registered_and_runs(db_session):
    assert get_definition("pest_alert") is not None
    assert get_definition("pest_alert")["enabled"] is True
    agent = get_global_registry().get("pest_alert")
    assert agent.code == "pest_alert"
    result = await agent.run({"query": "示例问题"}, _make_context(db_session))
    assert result.status == "succeeded"
    assert "示例问题" in result.output["answer"]
