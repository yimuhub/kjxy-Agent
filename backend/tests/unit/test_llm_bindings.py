"""模型绑定与兜底链测试（v3.25 B-a）。

- 解析顺序：助手指定 → 按智能体 → 按能力 → 小院/平台默认 → 小院生效 → 平台优先级；
- 供应商停用/自动降级时被跳过，链落到下一档；
- 运行时连续失败达阈值自动降级、成功即恢复；
- 写入校验：同层最多 3 条、供应商去重、能力码必须已登记；
- complete_with_logging 的兜底切换与逐档记账。
"""

import os

import pytest

os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-min-32-chars-for-unit-tests")
os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("EMBEDDING_PROVIDER", "mock")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("SUPPLIER_KEY_SECRET", Fernet.generate_key().decode())

from app.core.errors import AppError  # noqa: E402
from app.modules.llm import binding_service, supplier_service  # noqa: E402
from app.modules.llm.binding_models import SCOPE_AGENT, SCOPE_GLOBAL, SCOPE_PURPOSE  # noqa: E402
from app.modules.llm.binding_schemas import BindingsReplace  # noqa: E402
from app.modules.llm.models import LLMCall  # noqa: E402
from app.modules.llm.schemas import ChatMessage, LLMMetadata, LLMRequest  # noqa: E402
from app.modules.llm.supplier_schemas import STATUS_DISABLED, SupplierCreate  # noqa: E402

YARD = "t-binding"


def _mk_supplier(db, name: str, *, priority: int = 0):
    return supplier_service.create_supplier(
        db,
        YARD,
        SupplierCreate(
            name=name,
            provider_type="deepseek",
            base_url="https://api.example.test",
            model_id=f"{name}-model",
            api_key="sk-unit-test-key",
            priority=priority,
        ),
        is_platform_admin=False,
    )


def _bind(db, scope_type: str, scope_key: str, items: list[tuple[str, int]]):
    """items = [(supplier_id, priority)]，走真实替换语义。"""
    return binding_service.replace_bindings(
        db,
        YARD,
        BindingsReplace(
            scope_type=scope_type,
            scope_key=scope_key,
            entries=[{"supplier_id": sid, "priority": p} for sid, p in items],
        ),
        is_platform_admin=False,
    )


def _chain_ids(db, **kw) -> list[str]:
    return [c.supplier.id for c in binding_service.resolve_chain(db, YARD, **kw)]


# ---------- 解析顺序 ----------


def test_default_binding_beats_yard_active_supplier(db_session):
    active = _mk_supplier(db_session, "active", priority=50)
    fallback = _mk_supplier(db_session, "bound", priority=1)
    supplier_service.set_active_supplier(db_session, YARD, active.id)

    _bind(db_session, SCOPE_GLOBAL, "", [(fallback.id, 100), (active.id, 90)])

    ids = _chain_ids(db_session)
    assert ids[0] == fallback.id  # 默认绑定优先于「小院生效供应商」
    assert ids.index(active.id) > 0


def test_purpose_binding_precedes_global_and_agent_precedes_purpose(db_session):
    a = _mk_supplier(db_session, "global")
    b = _mk_supplier(db_session, "purpose")
    c = _mk_supplier(db_session, "agent")
    d = _mk_supplier(db_session, "assistant-field")

    _bind(db_session, SCOPE_GLOBAL, "", [(a.id, 10)])
    _bind(db_session, SCOPE_PURPOSE, "chat_document", [(b.id, 10)])
    _bind(db_session, SCOPE_AGENT, "agent-1", [(c.id, 10)])

    # 无 purpose/agent 上下文：只有默认档 + 平台优先级
    assert _chain_ids(db_session, purpose=None)[:1] == [a.id]
    # 能力绑定排在默认之前
    assert _chain_ids(db_session, purpose="chat_document")[:2] == [b.id, a.id]
    # 智能体绑定排在能力之前
    assert _chain_ids(db_session, purpose="chat_document", agent_code="agent-1")[:3] == [
        c.id,
        b.id,
        a.id,
    ]
    # 助手自身指定的供应商是最高一档
    chain = _chain_ids(
        db_session, purpose="chat_document", agent_code="agent-1", bound_supplier_id=d.id
    )
    assert chain[0] == d.id


def test_supplier_appears_once_across_levels_and_disabled_is_skipped(db_session):
    """跨层去重 + 不可用即跳过：同一供应商在能力层与默认层都出现时只算一次；
    停用的供应商不会占用候选位，链自动落到下一档。"""
    shared = _mk_supplier(db_session, "shared")
    good = _mk_supplier(db_session, "good")
    bad = _mk_supplier(db_session, "bad")
    _bind(db_session, SCOPE_PURPOSE, "chat_plan", [(shared.id, 10)])
    _bind(db_session, SCOPE_GLOBAL, "", [(shared.id, 100), (good.id, 90)])

    assert _chain_ids(db_session, purpose="chat_plan") == [shared.id, good.id]

    # 把主用换成一个已停用的供应商：解析直接跳过它，不会出现「全链不可用」
    _bind(db_session, SCOPE_PURPOSE, "chat_plan", [(bad.id, 10)])
    supplier_service.set_supplier_status(db_session, YARD, bad.id, STATUS_DISABLED)
    assert _chain_ids(db_session, purpose="chat_plan") == [shared.id, good.id]


def test_yard_owned_supplier_not_used_as_platform_fallback(db_session):
    """他院/自有供应商不会自动进入平台优先级兜底（成本与数据隔离口径）。"""
    mine = _mk_supplier(db_session, "mine", priority=999)
    ids = _chain_ids(db_session, purpose="declarative")
    assert ids == []  # 未显式绑定/设为生效时，只回落到全局优先级与 settings 兜底
    assert mine.id not in ids


# ---------- 兜底顺序与降级记账 ----------


def test_fallback_order_follows_priority_desc(db_session):
    first = _mk_supplier(db_session, "first")
    second = _mk_supplier(db_session, "second")
    _bind(db_session, SCOPE_GLOBAL, "", [(first.id, 20), (second.id, 10)])
    rows = binding_service.list_bindings(db_session, YARD, scope_type=SCOPE_GLOBAL)
    assert [r.supplier_id for r in rows] == [first.id, second.id]


def test_runtime_failures_degrade_after_threshold(db_session):
    row = _mk_supplier(db_session, "flaky")
    for i in range(supplier_service.RUNTIME_DEGRADE_FAILURES - 1):
        assert supplier_service.record_runtime_failure(db_session, row.id, f"boom {i}") is False
    fresh = supplier_service.get_supplier(db_session, YARD, row.id)
    assert fresh.status != STATUS_DISABLED
    assert fresh.consecutive_failures == supplier_service.RUNTIME_DEGRADE_FAILURES - 1

    assert supplier_service.record_runtime_failure(db_session, row.id, "boom last") is True
    assert supplier_service.get_supplier(db_session, YARD, row.id).status == STATUS_DISABLED
    # 降级后立刻从候选链消失
    _bind(db_session, SCOPE_GLOBAL, "", [(row.id, 10)])
    assert _chain_ids(db_session) == []


def test_runtime_success_resets_counter(db_session):
    row = _mk_supplier(db_session, "recovering")
    supplier_service.record_runtime_failure(db_session, row.id, "timeout")
    supplier_service.record_runtime_success(db_session, row.id)
    fresh = supplier_service.get_supplier(db_session, YARD, row.id)
    assert fresh.consecutive_failures == 0
    assert fresh.last_error is None


# ---------- 写入校验 ----------


def test_replace_bindings_rejects_too_many_and_duplicates(db_session):
    rows = [_mk_supplier(db_session, f"s{i}") for i in range(4)]
    with pytest.raises(AppError) as exc:
        _bind(db_session, SCOPE_GLOBAL, "", [(r.id, 10) for r in rows])
    assert exc.value.code == "BINDING_TOO_MANY"

    with pytest.raises(AppError) as dup:
        _bind(db_session, SCOPE_GLOBAL, "", [(rows[0].id, 10), (rows[0].id, 5)])
    assert dup.value.code == "INVALID_REQUEST"


def test_replace_bindings_rejects_unknown_purpose(db_session):
    row = _mk_supplier(db_session, "only")
    with pytest.raises(AppError) as exc:
        _bind(db_session, SCOPE_PURPOSE, "not_a_capability", [(row.id, 10)])
    assert exc.value.code == "INVALID_REQUEST"


def test_replace_bindings_requires_supplier_visibility(db_session):
    other = supplier_service.create_supplier(
        db_session,
        "t-other",
        SupplierCreate(
            name="private-other",
            provider_type="deepseek",
            base_url="https://api.example.test",
            model_id="m",
            api_key="sk-other",
        ),
        is_platform_admin=False,
    )
    with pytest.raises(AppError) as exc:
        _bind(db_session, SCOPE_GLOBAL, "", [(other.id, 10)])
    assert exc.value.code == "SUPPLIER_NOT_FOUND"


def test_replace_is_wholesale_and_keeps_at_most_one_chain(db_session):
    a = _mk_supplier(db_session, "a")
    b = _mk_supplier(db_session, "b")
    _bind(db_session, SCOPE_GLOBAL, "", [(a.id, 10), (b.id, 5)])
    _bind(db_session, SCOPE_GLOBAL, "", [(b.id, 10)])  # 整层替换：a 被淘汰
    assert [r.supplier_id for r in binding_service.list_bindings(db_session, YARD)] == [b.id]


# ---------- 运行时兜底切换 ----------


class _Failing:
    provider_name = "supplier:flaky"
    supplier_id = None

    def __init__(self, supplier_id: str | None = None) -> None:
        self.supplier_id = supplier_id

    async def complete(self, request):
        raise AppError("LLM_ERROR", "主用超时", http_status=502, details={"category": "timeout"})


class _Ok:
    provider_name = "supplier:spare"

    def __init__(self, supplier_id: str | None = None) -> None:
        self.supplier_id = supplier_id

    async def complete(self, request):
        from app.modules.llm.schemas import LLMResponse

        return LLMResponse(content="备用供应商答上了", model="spare-model", latency_ms=12)


@pytest.mark.asyncio
async def test_complete_with_logging_switches_to_fallback(db_session, monkeypatch):
    import app.modules.llm.service as llm_service

    row = _mk_supplier(db_session, "flaky-real")
    spare = _mk_supplier(db_session, "spare-real")
    _bind(db_session, SCOPE_GLOBAL, "", [(row.id, 10), (spare.id, 5)])

    monkeypatch.setattr(
        llm_service,
        "get_provider_candidates",
        lambda: [(_Failing(row.id), "yard_default"), (_Ok(spare.id), "yard_active")],
    )
    request = LLMRequest(
        messages=[ChatMessage(role="user", content="帮我把方案写完")],
        metadata=LLMMetadata(yard_id=YARD, request_id="req-fallback", purpose="chat_plan"),
    )
    resp = await llm_service.complete_with_logging(db_session, request)

    assert resp.content == "备用供应商答上了"
    calls = db_session.query(LLMCall).order_by(LLMCall.created_at.asc()).all()
    assert [c.status for c in calls] == ["error", "success"]
    assert calls[0].error_category == "timeout"
    # 兜底成功同样按供应商/能力归集（成本可分账），且不清零失败供应商的计数
    assert calls[1].supplier_id == spare.id
    assert calls[1].purpose == "chat_plan"
    # 一次真实失败即累计降级计数
    assert supplier_service.get_supplier(db_session, YARD, row.id).consecutive_failures == 1


@pytest.mark.asyncio
async def test_all_candidates_failing_reraises_last_error(db_session, monkeypatch):
    import app.modules.llm.service as llm_service

    monkeypatch.setattr(
        llm_service,
        "get_provider_candidates",
        lambda: [(_Failing(), "yard_default"), (_Failing(), "yard_active")],
    )
    request = LLMRequest(
        messages=[ChatMessage(role="user", content="还在吗")],
        metadata=LLMMetadata(yard_id=YARD, request_id="req-all-fail"),
    )
    with pytest.raises(AppError) as exc:
        await llm_service.complete_with_logging(db_session, request)
    assert exc.value.code == "LLM_ERROR"
    assert db_session.query(LLMCall).count() == 2  # 每档失败都留痕


# ---------- 「这次会用谁」 ----------


def test_resolution_summary_explains_each_hop(db_session):
    primary = _mk_supplier(db_session, "primary")
    backup = _mk_supplier(db_session, "backup")
    _bind(db_session, SCOPE_GLOBAL, "", [(primary.id, 20), (backup.id, 10)])

    summary = binding_service.resolution_summary(db_session, YARD, purpose="chat_document")
    assert summary["resolved_supplier_id"] == primary.id
    assert summary["purpose_label"] == "对话内文档生成"
    assert [h["supplier_id"] for h in summary["hops"]] == [primary.id, backup.id]
    assert summary["hops"][0]["level_label"] == "小院默认+兜底"
