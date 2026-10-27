"""技能 Agent 工具化（v3.3）单元测试：注册、执行与高风险安装闸。

工具执行走 tool_registry 全链路（schema 校验 → 权限 → 风险闸 → 审计），
断言的安全性质：skill.install 是 external_side_effect，未获批时自动建审批单
并拒绝执行（TOOL_RISK_BLOCKED），不存在绕过审核的安装入口。
"""

from __future__ import annotations

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.market import skill_agent
from app.modules.market.skill_tools import register_skill_agent_tools
from app.modules.tools.registry import tool_registry
from app.modules.yards.repository import create_yard

YARD = "yard-skill-tools"


@pytest.fixture(autouse=True)
def _registered_tools():
    """注册幂等：重复注册不报错、不重复。"""
    register_skill_agent_tools()
    register_skill_agent_tools()
    yield


@pytest.fixture(autouse=True)
def _reset_quota():
    """技能生成配额是进程级状态，用例间必须隔离。"""
    skill_agent._quota.clear()
    yield
    skill_agent._quota.clear()


@pytest.fixture()
def offline(monkeypatch):
    """GitHub 与联网搜索都不可用（不产生真实网络请求）。"""
    from app.modules.market import skill_agent as mod

    def _no_github(query: str, limit: int = 10) -> list:
        raise AppError("NETWORK_UNAVAILABLE", "当前设备无网络连接。", http_status=503)

    async def _no_web(db, yard_id, query, count=5, lang="zh-CN"):
        raise AppError("SEARCH_PROVIDER_UNAVAILABLE", "当前设备无网络连接。", http_status=503)

    monkeypatch.setattr(mod, "search_github_skills", _no_github)
    monkeypatch.setattr(mod, "search_web", _no_web)


def _ctx(roles: tuple[str, ...] = ("yard_admin",)) -> YardContext:
    return YardContext(yard_id=YARD, user_id="u-tools", roles=list(roles), request_id="req")


@pytest.fixture()
def _tool_db(db_session, monkeypatch):
    """工具执行函数自开会话：把 SessionLocal 指到测试内存库（与 knowledge.search 同法）。"""
    from app.db import session as session_module

    monkeypatch.setattr(session_module, "SessionLocal", lambda: db_session)


def test_registers_three_skill_tools():
    tools = {t.code: t for t in tool_registry.list_tools() if t.code.startswith("skill.")}
    assert set(tools) == {"skill.search", "skill.generate", "skill.install"}
    assert tools["skill.search"].risk_level == "read_only"
    assert tools["skill.search"].side_effect is False
    assert tools["skill.generate"].risk_level == "draft"
    assert tools["skill.install"].risk_level == "external_side_effect"
    assert tools["skill.install"].required_permissions == ["market:install"]
    assert tools["skill.generate"].required_permissions == ["skills:generate"]


async def test_skill_search_executes_through_registry(db_session, _tool_db):
    create_yard(db_session, name="技能工具小院")
    db_session.commit()
    result = await tool_registry.execute(
        db_session,
        "skill.search",
        {"utterance": "我需要一个土壤健康管理技能"},
        _ctx(),
        "req-search",
    )
    assert result["recommend"] == "install"
    assert result["matches"], result
    assert result["matches"][0]["item_key"].startswith("builtin:")


async def test_skill_generate_creates_private_draft(db_session, _tool_db, offline):
    create_yard(db_session, name="技能工具小院")
    db_session.commit()
    result = await tool_registry.execute(
        db_session,
        "skill.generate",
        {"utterance": "做一个量子纠缠番茄嫁接技能", "use_web": False},
        _ctx(),
        "req-gen",
    )
    brief = result["skill"]
    assert brief["visibility"] == "private"
    assert brief["status"] == "draft"
    assert result["token_usage"]["model"]
    # 生成成功即消耗一次共享配额
    assert skill_agent._quota[(YARD, skill_agent._today())] == 1


async def test_skill_install_blocked_without_risk_approval(db_session):
    """external_side_effect 闸：未获批先自动建审批单并拒绝执行。"""
    create_yard(db_session, name="技能工具小院")
    db_session.commit()
    with pytest.raises(AppError) as err:
        await tool_registry.execute(
            db_session,
            "skill.install",
            {"item_key": "builtin:skill:agri_pest_diagnosis"},
            _ctx(),
            "req-install",
        )
    assert err.value.code == "TOOL_RISK_BLOCKED"
    assert err.value.details.get("approval_id")
    # 审批单已创建且是 pending，等人工批
    from app.modules.tools.approvals import has_approved_approval

    assert (
        has_approved_approval(
            db_session,
            yard_id=YARD,
            tool_code="skill.install",
            params={"item_key": "builtin:skill:agri_pest_diagnosis"},
        )
        is False
    )


async def test_skill_tools_denied_without_permission(db_session):
    create_yard(db_session, name="技能工具小院")
    db_session.commit()
    with pytest.raises(AppError) as err:
        await tool_registry.execute(
            db_session,
            "skill.generate",
            {"utterance": "做一个技能", "use_web": False},
            _ctx(roles=("service_worker",)),
            "req-denied",
        )
    assert err.value.code == "TOOL_FORBIDDEN"


async def test_skill_search_validates_params(db_session):
    create_yard(db_session, name="技能工具小院")
    db_session.commit()
    with pytest.raises(AppError) as err:
        await tool_registry.execute(db_session, "skill.search", {}, _ctx(), "req-bad")
    assert err.value.code == "TOOL_VALIDATION_FAILED"
