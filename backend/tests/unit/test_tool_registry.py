"""ToolRegistry 机制测试。

app.modules.yards.permissions 由小院 Agent 另行交付；测试期若缺失则用本地
最小映射兜底（正式代码直接 import 该模块，见完成报告）。
"""

import asyncio
import sys
import types

import pytest

try:
    from app.modules.yards import permissions as _permissions  # noqa: F401
except ImportError:
    _stub = types.ModuleType("app.modules.yards.permissions")

    def has_permission(roles, permission: str) -> bool:
        # 与 app.modules.yards.permissions 同签名：单个权限点判定
        return permission in {"knowledge:read"} or "admin" in roles

    _stub.has_permission = has_permission
    sys.modules["app.modules.yards.permissions"] = _stub

from app.core.deps import YardContext  # noqa: E402
from app.core.errors import AppError  # noqa: E402
from app.modules.audit.models import AuditLog  # noqa: E402
from app.modules.tools import models as tool_models  # noqa: E401,F401 触发建表
from app.modules.tools.models import ToolDefinition  # noqa: E402
from app.modules.tools.registry import (  # noqa: E402
    ToolDef,
    ToolRegistry,
    seed_builtin_tools,
)


def _ctx(roles=("admin",)) -> YardContext:
    return YardContext(yard_id="t-001", user_id="u-001", roles=list(roles), request_id="req-test")


def _echo_def(**kw) -> ToolDef:
    async def echo(params, ctx):
        return {"echo": params.get("msg"), "user": ctx.user_id}

    d = dict(
        code="test.echo",
        version="1.0.0",
        name="回声",
        description="测试用",
        input_schema={
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
            "additionalProperties": False,
        },
        func=echo,
        required_permissions=[],
        timeout_seconds=5,
    )
    d.update(kw)
    return ToolDef(**d)


def test_register_get_list():
    reg = ToolRegistry()
    reg.register(_echo_def())
    reg.register(_echo_def(code="test.other", required_permissions=["x:read"]))
    assert reg.get("test.echo").code == "test.echo"
    assert [t.code for t in reg.list_tools()] == ["test.echo", "test.other"]


def test_get_unknown_tool():
    reg = ToolRegistry()
    with pytest.raises(AppError) as e:
        reg.get("no.such.tool")
    assert e.value.code == "TOOL_NOT_FOUND"
    assert e.value.http_status == 404


def test_get_disabled_tool():
    reg = ToolRegistry()
    reg.register(_echo_def(enabled=False))
    with pytest.raises(AppError) as e:
        reg.get("test.echo")
    assert e.value.code == "TOOL_NOT_FOUND"


@pytest.mark.asyncio
async def test_validation_failed(db_session):
    reg = ToolRegistry()
    reg.register(_echo_def())
    with pytest.raises(AppError) as e:
        await reg.execute(db_session, "test.echo", {"wrong": 1}, _ctx(), "req-1")
    assert e.value.code == "TOOL_VALIDATION_FAILED"
    assert e.value.http_status == 400
    assert e.value.details["fields"]  # details 含字段错误


@pytest.mark.asyncio
async def test_unknown_tool_execute(db_session):
    reg = ToolRegistry()
    with pytest.raises(AppError) as e:
        await reg.execute(db_session, "no.such", {}, _ctx(), "req-1")
    assert e.value.code == "TOOL_NOT_FOUND"


@pytest.mark.asyncio
async def test_permission_denied_and_audited(db_session):
    reg = ToolRegistry()
    reg.register(_echo_def(required_permissions=["knowledge:read"]))
    with pytest.raises(AppError) as e:
        await reg.execute(db_session, "test.echo", {"msg": "hi"}, _ctx(roles=["user"]), "req-2")
    assert e.value.code == "TOOL_FORBIDDEN"
    assert e.value.http_status == 403
    db_session.commit()
    audit = db_session.query(AuditLog).filter_by(request_id="req-2").one()
    assert audit.action == "tool_executed"
    assert audit.result == "denied"


@pytest.mark.asyncio
async def test_risk_blocked_without_approval(db_session):
    async def dangerous(params, ctx):
        return {"done": True}

    reg = ToolRegistry()
    reg.register(
        _echo_def(
            code="test.danger",
            func=dangerous,
            risk_level="external_side_effect",
            input_schema={"type": "object"},
        )
    )
    with pytest.raises(AppError) as e:
        await reg.execute(db_session, "test.danger", {}, _ctx(), "req-3")
    assert e.value.code == "TOOL_RISK_BLOCKED"
    assert e.value.http_status == 403


@pytest.mark.asyncio
async def test_client_approved_param_cannot_bypass_approval(db_session):
    """请求参数中的 approved 由调用方控制，绝不能作为审批放行依据。"""

    async def dangerous(params, ctx):
        return {"done": True}

    reg = ToolRegistry()
    reg.register(
        _echo_def(
            code="test.danger",
            func=dangerous,
            risk_level="external_side_effect",
            input_schema={"type": "object"},
        )
    )
    with pytest.raises(AppError) as e:
        await reg.execute(db_session, "test.danger", {"approved": True}, _ctx(), "req-4")
    assert e.value.code == "TOOL_RISK_BLOCKED"


@pytest.mark.asyncio
async def test_timeout(db_session):
    async def slow(params, ctx):
        await asyncio.sleep(30)
        return {}

    reg = ToolRegistry()
    reg.register(
        _echo_def(
            code="test.slow", func=slow, input_schema={"type": "object"}, timeout_seconds=0.05
        )
    )
    with pytest.raises(AppError) as e:
        await reg.execute(db_session, "test.slow", {}, _ctx(), "req-5")
    assert e.value.code == "TOOL_TIMEOUT"
    assert e.value.http_status == 504


@pytest.mark.asyncio
async def test_sync_tool_timeout_and_loop_responsive(db_session):
    """同步工具超时必须兑现，且不阻塞事件循环（审查 S3）。"""
    import threading
    import time as _time

    release = threading.Event()

    def slow_sync(params, ctx):
        release.wait(5)  # 模拟慢系统调用；超时后线程仍存活属预期
        return {}

    reg = ToolRegistry()
    reg.register(
        _echo_def(
            code="test.slow_sync",
            func=slow_sync,
            input_schema={"type": "object"},
            timeout_seconds=0.05,
        )
    )
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        for _ in range(20):
            await asyncio.sleep(0.01)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    started = _time.perf_counter()
    try:
        with pytest.raises(AppError) as e:
            await reg.execute(db_session, "test.slow_sync", {}, _ctx(), "req-sync-t")
    finally:
        release.set()
        await beat
    elapsed = _time.perf_counter() - started
    assert e.value.code == "TOOL_TIMEOUT"
    assert elapsed < 1.0  # 不再等慢函数执行完
    assert ticks >= 3  # 超时等待期间事件循环仍在运转
    db_session.commit()
    audit = db_session.query(AuditLog).filter_by(request_id="req-sync-t").one()
    assert audit.metadata_json["side_effects_may_continue"] is True


@pytest.mark.asyncio
async def test_sync_tool_success_runs(db_session):
    def echo_sync(params, ctx):
        return {"echo": params.get("msg"), "user": ctx.user_id}

    reg = ToolRegistry()
    reg.register(_echo_def(code="test.echo_sync", func=echo_sync))
    out = await reg.execute(db_session, "test.echo_sync", {"msg": "hi"}, _ctx(), "req-sync-ok")
    assert out == {"echo": "hi", "user": "u-001"}


@pytest.mark.asyncio
async def test_success_audits_param_keys_only(db_session):
    reg = ToolRegistry()
    reg.register(_echo_def(input_schema={"type": "object"}))
    out = await reg.execute(
        db_session, "test.echo", {"msg": "hello", "secret_field": "s3cr3t"}, _ctx(), "req-6"
    )
    assert out == {"echo": "hello", "user": "u-001"}
    db_session.commit()
    audit = db_session.query(AuditLog).filter_by(request_id="req-6").one()
    assert audit.result == "success"
    assert audit.metadata_json["param_keys"] == ["msg", "secret_field"]
    assert "s3cr3t" not in str(audit.metadata_json)  # 不记敏感值


@pytest.mark.asyncio
async def test_output_schema_validation(db_session):
    async def bad(params, ctx):
        return {"not": "expected-shape"}

    reg = ToolRegistry()
    reg.register(
        _echo_def(
            code="test.badout",
            func=bad,
            input_schema={"type": "object"},
            output_schema={"type": "object", "required": ["echo"]},
        )
    )
    with pytest.raises(AppError) as e:
        await reg.execute(db_session, "test.badout", {}, _ctx(), "req-7")
    assert e.value.http_status == 500  # 工具实现 bug，属服务端错误


def test_seed_builtin_tools_idempotent(db_session):
    # v3.41：在内置 video.generate 基础上新增 doc.generate，种子共 5 项
    assert seed_builtin_tools(db_session) == 5
    assert seed_builtin_tools(db_session) == 5  # 第二次幂等
    db_session.commit()
    rows = db_session.query(ToolDefinition).filter_by(code="knowledge.search").all()
    assert len(rows) == 1
    assert rows[0].risk_level == "read_only"
    assert rows[0].required_permissions_json == ["knowledge:read"]
    doc_rows = db_session.query(ToolDefinition).filter_by(code="doc.generate").all()
    assert len(doc_rows) == 1
    assert doc_rows[0].risk_level == "draft"
    assert doc_rows[0].required_permissions_json == []
    workorder_rows = db_session.query(ToolDefinition).filter_by(code="workorder.dispatch").all()
    assert len(workorder_rows) == 1
    assert workorder_rows[0].risk_level == "external_side_effect"
    sensor_rows = db_session.query(ToolDefinition).filter_by(code="sensor.query").all()
    assert len(sensor_rows) == 1
    assert sensor_rows[0].risk_level == "read_only"
    assert sensor_rows[0].required_permissions_json == []
