"""声明式助手模型绑定（YardAgent.supplier_id 从死字段变为运行时生效）单元测试。

覆盖：
1. 预解析 _resolve_bound_supplier：本小院/全局启用供应商 → 取行；
   他院供应商/禁用/不存在 → None（权限双检 + 宁缺勿幻）；
2. 绑定生效：运行请求 LLMRequest.model=供应商 model_id、metadata.supplier_id
   透传，provider 走绑定供应商（llm_calls.provider 记 supplier:<name>）；
3. 他院/禁用供应商 → 静默回退既有解析链（fake 兜底），绝不向聊天用户抛错；
4. 未绑定 → 与改造前逐字一致（model=None、provider 走 settings/active 链）。
"""

import pytest
from app.core.deps import YardContext
from app.modules.agents import declarative
from app.modules.agents.models import STATUS_PUBLISHED, YardAgent
from app.modules.llm import providers_fake, supplier_service
from app.modules.llm.models import LLMCall
from app.modules.llm.provider import invalidate_supplier_cache
from app.modules.llm.supplier_models import Supplier

BOUND_MODEL = "bound-deepseek-v3"


@pytest.fixture(autouse=True)
def _cache_isolation():
    invalidate_supplier_cache()
    providers_fake.reset_scripted_tool_responses()
    yield
    invalidate_supplier_cache()
    providers_fake.reset_scripted_tool_responses()


@pytest.fixture()
def yard(db_session):
    from app.modules.yards.repository import create_yard

    t = create_yard(db_session, name="绑定小院")
    db_session.commit()
    return t


@pytest.fixture()
def other_yard(db_session):
    from app.modules.yards.repository import create_yard

    t = create_yard(db_session, name="别家绑定小院")
    db_session.commit()
    return t


def _mk_supplier(db, yard_id, *, name, model_id, status=1):
    row = Supplier(
        yard_id=yard_id,
        name=name,
        provider_type="deepseek",
        base_url="https://example.invalid/v1",
        model_id=model_id,
        api_key_enc="placeholder-not-used",  # provider 构建在本用例被 monkeypatch
        status=status,
    )
    db.add(row)
    db.commit()
    return row


def _mk_agent(db, yard_id, supplier_id=None) -> YardAgent:
    agent = YardAgent(
        yard_id=yard_id,
        agent_code="bound_helper",
        name="绑定助手",
        persona_prompt="你是测试助手。",
        task_prompt_template="请回答：{{question}}",
        allowed_tools=[],
        knowledge_scopes=["yard"],
        supplier_id=supplier_id,
        risk_level="low",
        status=STATUS_PUBLISHED,
        version="1.0.0",
    )
    db.add(agent)
    db.commit()
    return agent


def _ctx(yard_id):
    return YardContext(
        yard_id=yard_id, user_id="u-bind", roles=["yard_admin"], request_id="req-bind"
    )


def _fake_build(monkeypatch):
    """绑定供应商 provider 用 fake 替换（离线纪律）：保留 supplier 标签与模型位。"""

    def build(row):
        provider = providers_fake.FakeProvider(model=row.model_id)
        provider.provider_name = f"supplier:{row.name}"[:50]
        provider.supplier_id = row.id
        provider.supports_tools = False
        return provider

    monkeypatch.setattr(supplier_service, "build_provider_from_supplier", build)


# ---------------------------------------------------------------------------
# 1) 预解析（权限双检）
# ---------------------------------------------------------------------------


def test_resolve_bound_supplier_accepts_own_yard_and_global(db_session, yard):
    own = _mk_supplier(db_session, yard.id, name="own", model_id="m-own")
    glob = _mk_supplier(db_session, None, name="glob", model_id="m-glob")
    assert declarative._resolve_bound_supplier(db_session, yard.id, own.id) is own
    assert declarative._resolve_bound_supplier(db_session, yard.id, glob.id) is glob
    assert declarative._resolve_bound_supplier(db_session, yard.id, None) is None


def test_resolve_bound_supplier_rejects_foreign_disabled_missing(db_session, yard, other_yard):
    foreign = _mk_supplier(db_session, other_yard.id, name="foreign", model_id="m-f")
    disabled = _mk_supplier(db_session, yard.id, name="off", model_id="m-d", status=2)
    assert declarative._resolve_bound_supplier(db_session, yard.id, foreign.id) is None
    assert declarative._resolve_bound_supplier(db_session, yard.id, disabled.id) is None
    assert declarative._resolve_bound_supplier(db_session, yard.id, "no-such-id") is None


# ---------------------------------------------------------------------------
# 2) 绑定生效：模型与 provider 都走绑定供应商
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bound_supplier_used_at_runtime(db_session, yard, monkeypatch):
    supplier = _mk_supplier(db_session, yard.id, name="bound-main", model_id=BOUND_MODEL)
    agent = _mk_agent(db_session, yard.id, supplier.id)
    _fake_build(monkeypatch)

    result = await declarative.run_declarative_agent(
        db_session,
        agent=agent,
        input_data={"question": "今天浇水吗", "top_k": 1},
        ctx=_ctx(yard.id),
        task_id="run-bound",
    )
    assert result.output["model"] == BOUND_MODEL
    row = db_session.query(LLMCall).filter_by(task_id="run-bound").first()
    assert row.provider == "supplier:bound-main"
    assert row.model == BOUND_MODEL


# ---------------------------------------------------------------------------
# 3) 不可用绑定 → 回退既有链，不抛错
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["foreign", "disabled"])
async def test_unusable_binding_falls_back(db_session, yard, other_yard, bad):
    if bad == "foreign":
        supplier = _mk_supplier(db_session, other_yard.id, name="f", model_id="other-model")
    else:
        supplier = _mk_supplier(db_session, yard.id, name="d", model_id="off-model", status=2)
    agent = _mk_agent(db_session, yard.id, supplier.id)

    # 未 monkeypatch：绑定不可用 → 回退 settings=fake，正常收敛、无 5xx
    result = await declarative.run_declarative_agent(
        db_session,
        agent=agent,
        input_data={"question": "回退测试", "top_k": 1},
        ctx=_ctx(yard.id),
        task_id=f"run-fb-{bad}",
    )
    assert result.status == "succeeded"
    row = db_session.query(LLMCall).filter_by(task_id=f"run-fb-{bad}").first()
    assert row.provider == "fake"
    assert row.model != ("other-model" if bad == "foreign" else "off-model")


# ---------------------------------------------------------------------------
# 4) 未绑定 → 与改造前一致
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unbound_behaves_exactly_as_before(db_session, yard):
    agent = _mk_agent(db_session, yard.id, None)
    result = await declarative.run_declarative_agent(
        db_session,
        agent=agent,
        input_data={"question": "老路径", "top_k": 1},
        ctx=_ctx(yard.id),
        task_id="run-plain",
    )
    assert result.output["model"] == "fake-mock-1"  # settings=fake 兜底，原口径
    plain = db_session.query(LLMCall).filter_by(task_id="run-plain").first()
    assert "supplier" not in str(plain.provider)
