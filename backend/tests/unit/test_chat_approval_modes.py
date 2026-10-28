"""会话授权模式三档强制（ask / auto / full）。

口径（产品定稿）：
1. ``ask``=现状——高风险工具未获批即拦下并建审批单（行为逐字不变）；
2. ``auto``=在 ask 的现状之上再加一条：**本会话内**批准过的同一调用可以无限次复用
   （不受 ``tool_approval_max_uses`` 次数上限约束）；别的会话的单只按现状复用，
   用完次数就不再放行——借来的批准买不到额外复用；
3. ``full``=本会话高风险工具直接执行、不建审批单，但**只有小院/平台管理员生效**，
   普通成员即便会话里选了也按 ask 走，且放行必须单独留痕。

非对话入口（任务/机器人/脚本）不带会话，YardContext 缺省 approval_mode=ask，
所以本文件之外的全部既有审批测试必须照常通过。
"""

import asyncio

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.audit.models import AuditLog
from app.modules.chat.models import APPROVAL_AUTO, APPROVAL_FULL
from app.modules.tools.models import (
    APPROVAL_APPROVED,
    APPROVAL_PENDING,
    ToolApproval,
)
from app.modules.tools.registry import ToolDef, ToolRegistry

YARD = "yard-modes"


def _ctx(mode: str = "ask", session_id: str = "", roles=("member",)) -> YardContext:
    return YardContext(
        yard_id=YARD,
        user_id="u-1",
        roles=list(roles),
        request_id="req-modes",
        approval_mode=mode,
        session_id=session_id,
    )


def _dangerous_registry() -> ToolRegistry:
    calls: list[dict] = []

    async def dangerous(params, ctx):
        calls.append(dict(params))
        return {"done": True}

    reg = ToolRegistry()
    reg.register(
        ToolDef(
            code="test.danger",
            version="1.0.0",
            name="危险动作",
            description="测试用",
            input_schema={"type": "object"},
            func=dangerous,
            required_permissions=[],
            risk_level="external_side_effect",
            timeout_seconds=5,
        )
    )
    return reg


def _approved_ticket(
    db,
    *,
    params: dict,
    session_id: str | None,
    used_count: int = 0,
    status: str = APPROVAL_APPROVED,
):
    from app.modules.tools.approvals import params_hash

    row = ToolApproval(
        yard_id=YARD,
        tool_code="test.danger",
        params_json=params,
        params_hash=params_hash(params),
        status=status,
        requested_by="u-1",
        chat_session_id=session_id,
        used_count=used_count,
    )
    db.add(row)
    db.flush()
    return row


def _run(reg, db, ctx, params, code: str = "test.danger"):
    return asyncio.run(reg.execute(db, code, params, ctx, "req-modes"))


def test_ask_mode_is_unchanged_and_records_pending_ticket(db_session):
    """ask：无审批单即拦下、建 pending 单，并把 approval_id 记进本轮上下文。"""
    reg = _dangerous_registry()
    ctx = _ctx("ask", "sess-a")
    with pytest.raises(AppError) as e:
        _run(reg, db_session, ctx, {"x": 1})
    db_session.commit()
    assert e.value.code == "TOOL_RISK_BLOCKED"
    assert e.value.details["approval_mode"] == "ask"
    assert ctx.approval_requests == [
        {"tool_code": "test.danger", "approval_id": e.value.details["approval_id"], "reason": None}
    ]
    ticket = db_session.get(ToolApproval, e.value.details["approval_id"])
    assert ticket.status == APPROVAL_PENDING
    assert ticket.chat_session_id == "sess-a"


def test_full_mode_executes_for_admin_and_audits(db_session):
    """full + 管理员：直接执行、不建审批单，且单独留一条 auto 放行痕迹。"""
    reg = _dangerous_registry()
    result = _run(reg, db_session, _ctx(APPROVAL_FULL, "sess-a", roles=("yard_admin",)), {"x": 2})
    db_session.commit()
    assert result == {"done": True}
    assert db_session.query(ToolApproval).count() == 0
    trace = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "tool_risk_auto_approved")
        .order_by(AuditLog.created_at.desc())
        .first()
    )
    assert trace is not None
    assert trace.metadata_json["via"] == "session_full_access"
    assert trace.metadata_json["session_id"] == "sess-a"
    assert trace.result == "success"
    assert trace.metadata_json["executed_ok"] is True


def test_full_mode_still_blocks_for_plain_member(db_session):
    """full 是权限动作：普通成员即使在会话里选了，执行时仍按 ask 拦下。"""
    reg = _dangerous_registry()
    with pytest.raises(AppError) as e:
        _run(reg, db_session, _ctx(APPROVAL_FULL, "sess-a", roles=("member",)), {"x": 3})
    assert e.value.code == "TOOL_RISK_BLOCKED"


def test_auto_mode_reuses_same_session_approval_beyond_use_cap(db_session):
    """auto 的增量：本会话批准过的同一调用，即使复用次数已用满也继续放行。"""
    from app.core.config import settings

    _approved_ticket(db_session, params={"x": 4}, session_id="sess-a", used_count=9)
    db_session.commit()
    reg = _dangerous_registry()
    original_cap = settings.tool_approval_max_uses
    settings.tool_approval_max_uses = 1
    try:
        assert _run(reg, db_session, _ctx(APPROVAL_AUTO, "sess-a"), {"x": 4}) == {"done": True}
    finally:
        settings.tool_approval_max_uses = original_cap


def test_auto_mode_cannot_borrow_other_session_extra_reuse(db_session):
    """别的会话批准的单按现状复用：次数用满后 auto 也不再放行（不越权借复用）。"""
    from app.core.config import settings

    _approved_ticket(db_session, params={"x": 5}, session_id="sess-b", used_count=9)
    db_session.commit()
    reg = _dangerous_registry()
    original_cap = settings.tool_approval_max_uses
    settings.tool_approval_max_uses = 1
    try:
        with pytest.raises(AppError) as e:
            _run(reg, db_session, _ctx(APPROVAL_AUTO, "sess-a"), {"x": 5})
    finally:
        settings.tool_approval_max_uses = original_cap
    assert e.value.code == "TOOL_RISK_BLOCKED"


def test_auto_mode_first_call_without_any_approval_still_blocks(db_session):
    reg = _dangerous_registry()
    with pytest.raises(AppError) as e:
        _run(reg, db_session, _ctx(APPROVAL_AUTO, "sess-a"), {"x": 6})
    assert e.value.code == "TOOL_RISK_BLOCKED"


def test_approved_ticket_does_not_leak_across_sessions(db_session):
    """会话归属真正生效：A 会话批准的一单只认 A，别的会话与任务侧都借不到。

    0043 给审批单加上了 chat_session_id，但兜底查询若不看这个归属，
    「按会话批准」就会被悄悄放宽成「一处批准、全处可用」。
    """
    from app.core.config import settings

    _approved_ticket(db_session, params={"x": 7}, session_id="sess-a")
    db_session.commit()
    reg = _dangerous_registry()
    original = settings.tool_approval_max_uses
    settings.tool_approval_max_uses = 0  # 放开次数上限，只考归属这一件事
    try:
        assert _run(reg, db_session, _ctx("ask", "sess-a"), {"x": 7}) == {"done": True}
        for intruder in ("sess-b", ""):
            with pytest.raises(AppError) as e:
                _run(reg, db_session, _ctx("ask", intruder), {"x": 7})
            assert e.value.code == "TOOL_RISK_BLOCKED"
    finally:
        settings.tool_approval_max_uses = original


def test_unscoped_ticket_still_authorizes_any_caller(db_session):
    """没有会话归属的单（0043 之前的历史行、任务侧建的单）保持全院通用——既有契约不变。"""
    from app.core.config import settings

    _approved_ticket(db_session, params={"x": 8}, session_id=None)
    db_session.commit()
    reg = _dangerous_registry()
    original = settings.tool_approval_max_uses
    settings.tool_approval_max_uses = 0
    try:
        for sid in ("sess-a", "sess-b", ""):
            assert _run(reg, db_session, _ctx("ask", sid), {"x": 8}) == {"done": True}
    finally:
        settings.tool_approval_max_uses = original


def test_full_mode_bypass_audit_records_real_outcome(db_session):
    """免批准放行的留痕写在执行之后，记的是真实结局：工具跑挂了不能记成 success。

    full 档不产生审批单，这两条留痕是该调用唯一的书面证据。
    """

    async def boom(params, ctx):
        raise RuntimeError("外部系统炸了")

    reg = ToolRegistry()
    reg.register(
        ToolDef(
            code="test.boom",
            version="1.0.0",
            name="会炸的动作",
            description="测试用",
            input_schema={"type": "object"},
            func=boom,
            required_permissions=[],
            risk_level="external_side_effect",
            timeout_seconds=5,
        )
    )
    with pytest.raises(AppError):
        _run(
            reg,
            db_session,
            _ctx(APPROVAL_FULL, "sess-a", roles=("yard_admin",)),
            {},
            code="test.boom",
        )
    db_session.commit()
    trace = db_session.query(AuditLog).filter(AuditLog.action == "tool_risk_auto_approved").one()
    assert trace.result == "failure"
    assert trace.metadata_json["executed_ok"] is False
    assert trace.metadata_json["via"] == "session_full_access"


@pytest.mark.parametrize("mode", ["ask", APPROVAL_AUTO, APPROVAL_FULL])
def test_read_only_tools_are_never_gated(db_session, mode):
    """授权模式只管高风险工具：read_only 工具在三档下都照常执行。"""

    async def echo(params, ctx):
        return {"ok": True}

    reg = ToolRegistry()
    reg.register(
        ToolDef(
            code="test.safe",
            version="1.0.0",
            name="只读",
            description="测试用",
            input_schema={"type": "object"},
            func=echo,
            required_permissions=[],
            risk_level="read_only",
            timeout_seconds=5,
        )
    )
    assert _run(reg, db_session, _ctx(mode, "sess-a"), {}, code="test.safe") == {"ok": True}


def test_legacy_callers_without_session_default_to_ask(db_session):
    """老调用方（任务/机器人/脚本）不传新字段 → 缺省 ask，行为零变化。"""
    ctx = YardContext(yard_id=YARD, user_id="u-1", roles=["member"], request_id="req-legacy")
    assert (ctx.approval_mode, ctx.session_id, ctx.approval_requests) == ("ask", "", [])


def test_scoped_ctx_snapshots_the_session_policy(db_session):
    """对话入口把会话的授权模式与会话 id 带进本轮上下文，且给本轮一个新的留痕列表。"""
    from app.modules.chat import service as chat_service
    from app.modules.chat.models import ChatSession

    sess = ChatSession(
        yard_id=YARD, created_by="u-1", title="策略会话", approval_mode=APPROVAL_AUTO
    )
    db_session.add(sess)
    db_session.flush()

    base = _ctx("ask", "", roles=("member",))
    base.approval_requests.append({"junk": True})
    scoped = chat_service.scoped_ctx(base, sess)
    assert (scoped.approval_mode, scoped.session_id) == (APPROVAL_AUTO, sess.id)
    assert scoped.approval_requests == [], "每轮单独留痕，不能串到上一轮"
    assert base.approval_mode == "ask", "原上下文不被改写"


def test_approval_card_shape_lists_only_real_ticket_ids():
    from app.modules.chat import service as chat_service

    card = chat_service.approval_card(
        [{"tool_code": "workorder.dispatch", "approval_id": "ap-1"}, {"tool_code": "x"}]
    )
    assert card["type"] == "approval"
    assert card["items"] == [{"tool_code": "workorder.dispatch", "approval_id": "ap-1"}]


def test_fork_helper_remaps_dict_and_bare_attachment_refs():
    """分支的附件引用：dict 形态换 id，裸 id 形态也换，映射不到的原样保留。"""
    from app.modules.chat import service as chat_service

    mapping = {"old": "new"}
    assert chat_service._remap_attachment_ref({"id": "old", "filename": "a.txt"}, mapping) == {
        "id": "new",
        "filename": "a.txt",
    }
    assert chat_service._remap_attachment_ref("old", mapping) == "new"
    assert chat_service._remap_attachment_ref({"id": "other"}, mapping) == {"id": "other"}
    assert chat_service._attachment_ref_id({"id": "old"}) == "old"
    assert chat_service._attachment_ref_id("old") == "old"
    assert chat_service._attachment_ref_id(None) == ""
