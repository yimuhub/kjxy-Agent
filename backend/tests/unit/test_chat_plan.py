"""对话内方案合成（v3.14）：多源取证、完整性契约、缺章补写、截断续写与诚实标注。"""

import asyncio

import pytest
from app.core.errors import AppError
from app.core.security import create_access_token
from app.modules.agents.teams import research as team_research
from app.modules.auth import repository as auth_repo
from app.modules.chat import plan as plan_module
from app.modules.chat import service as chat_service
from app.modules.yards import repository as yard_repo

SECTION_NAMES = [name for name, _ in plan_module.SECTIONS]


@pytest.fixture()
def world(db_session):
    yard = yard_repo.create_yard(db_session, name="方案小院")
    owner = auth_repo.create_user(db_session, username="plan-owner", password="pw-plan")
    other = auth_repo.create_user(db_session, username="plan-other", password="pw-plan")
    yard_repo.add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="operator")
    yard_repo.add_membership(db_session, yard_id=yard.id, user_id=other.id, role="operator")
    db_session.commit()
    session = chat_service.create_session(
        db_session, yard_id=yard.id, created_by=owner.id, title="方案会话"
    )
    db_session.commit()
    return {"yard": yard, "owner": owner, "other": other, "session": session}


def _ctx(world):
    from app.core.deps import YardContext

    return YardContext(
        user_id=world["owner"].id,
        yard_id=world["yard"].id,
        roles=["operator"],
        request_id="test-request",
    )


def _hdr(user, yard) -> dict[str, str]:
    token = create_access_token(user_id=user.id, yard_id=yard.id, roles=["operator"])
    return {"Authorization": f"Bearer {token}"}


def _draft(
    headings=SECTION_NAMES,
    body="分蘖期保持浅水勤灌，忌长期深水淹灌；全田分蘖数达计划穗数八成时及时晒田，"
    "以叶色回落与田间水位记录作为复水时机判定标准。",
) -> str:
    return "\n\n".join(f"## {name}\n{body}" for name in headings)


class _Resp:
    def __init__(self, content: str, finish_reason: str = "stop") -> None:
        self.content = content
        self.finish_reason = finish_reason
        self.input_tokens = 120
        self.output_tokens = 900
        self.model = "fake-model"


EVIDENCE = {
    "knowledge": [
        {
            "document_id": "doc-1",
            "chunk_id": "chunk-1",
            "title": "水稻分蘖期管理技术要点",
            "source": "seed",
            "page_number": 1,
            "quote": "分蘖期保持浅水勤灌，忌长期深水淹灌。",
            "score": 0.81,
        }
    ],
    "web": [
        {
            "title": "赤霉病防治提示",
            "url": "https://example.com/wheat",
            "snippet": "扬花期遇雨需及时施药并遵守安全间隔期。",
        }
    ],
    "warnings": [],
}


def _stub_gather(monkeypatch, evidence=None):
    calls: dict[str, object] = {}

    async def _fake_collect(db, *, ctx, question, **kwargs):
        calls["kwargs"] = kwargs
        return {**EVIDENCE, **(evidence or {})}

    monkeypatch.setattr(plan_module, "collect_research", _fake_collect)
    return calls


def _stub_llm(monkeypatch, responses):
    """按脚本依次返回模型输出，并记录每次调用的提示词与参数。"""
    seen: list[dict] = []
    queue = list(responses)

    async def _fake(db, request):
        seen.append(request)
        item = queue.pop(0) if queue else _Resp("")
        if isinstance(item, tuple):
            text, finish = item
            return _Resp(text, finish)
        return _Resp(item)

    monkeypatch.setattr(plan_module, "complete_with_logging", _fake)
    monkeypatch.setattr(plan_module, "select_relevant_skill_codes", lambda db, **_: [])
    return seen


def test_plan_endpoint_synthesizes_complete_answer_with_citations(
    client, db_session, world, monkeypatch
):
    _stub_gather(monkeypatch)
    calls = _stub_llm(monkeypatch, [_draft()])
    res = client.post(
        f"/api/v1/chat/sessions/{world['session'].id}/messages",
        json={"content": "写一份小麦赤霉病的防治方案"},
        headers=_hdr(world["owner"], world["yard"]),
    )
    assert res.status_code == 200, res.text
    assistant = res.json()["data"]["assistant_message"]
    # 方案正文当场可见，不再是一句「已生成文档」
    assert assistant["routed_by"] == "plan"
    assert assistant["routed_label"] == "多源方案"
    assert "## 实施步骤" in assistant["content"]
    assert len(assistant["content"]) > 300
    payload = assistant["payload"]
    assert payload["type"] == "plan"
    assert payload["complete"] is True
    assert len(payload["sections"]) >= len(SECTION_NAMES)
    assert payload["missing_sections"] == []
    assert payload["needs_expert_review"] is False
    kinds = {c["kind"] for c in assistant["citations"]}
    assert kinds == {"knowledge", "web"}
    refs = {c["reference"] for c in assistant["citations"]}
    assert "K1" in refs and "W1" in refs
    assert assistant["reason"].startswith("知识库 1 条 · 联网 1 条")
    assert len(calls) == 1


def test_plan_prompt_carries_contract_and_honesty_rules(client, db_session, world, monkeypatch):
    _stub_gather(monkeypatch)
    calls = _stub_llm(monkeypatch, [_draft()])
    res = client.post(
        f"/api/v1/chat/sessions/{world['session'].id}/messages",
        json={"content": "写一份水稻分蘖期管理方案"},
        headers=_hdr(world["owner"], world["yard"]),
    )
    assert res.status_code == 200, res.text
    system = calls[0].messages[0].content
    user = calls[0].messages[1].content
    for phrase in ("[K1]", "模型推断，需当地核实", "不得自拟品种、剂量", "待确认事项"):
        assert phrase in system
    assert "<plan_evidence>" in user and "<attachments>" not in user
    assert calls[0].max_tokens == plan_module.settings.plan_max_tokens


def test_plan_repairs_missing_sections_then_reports_completion(
    client, db_session, world, monkeypatch
):
    _stub_gather(monkeypatch)
    missing = SECTION_NAMES[:5]
    filler = (
        "扬花期遇连续两天以上降雨需提前施药，施后六小时内遇雨补喷，并记录药剂登记编号与安全间隔期。"
    )
    patch = "\n\n".join(f"## {name}\n{filler}" for name in SECTION_NAMES[5:])
    calls = _stub_llm(monkeypatch, [_draft(missing), patch])
    res = client.post(
        f"/api/v1/chat/sessions/{world['session'].id}/messages",
        json={"content": "制定明年基地扩建规划"},
        headers=_hdr(world["owner"], world["yard"]),
    )
    assert res.status_code == 200, res.text
    assistant = res.json()["data"]["assistant_message"]
    assert len(calls) == 2
    assert assistant["payload"]["repair_passes"] == 1
    assert assistant["payload"]["complete"] is True
    assert "## 验收指标" in assistant["content"]


def test_plan_still_incomplete_is_flagged_not_fabricated(client, db_session, world, monkeypatch):
    _stub_gather(monkeypatch)
    _stub_llm(monkeypatch, ["## 目标与适用条件\n提高产量。", "## 目标与适用条件\n略"])
    res = client.post(
        f"/api/v1/chat/sessions/{world['session'].id}/messages",
        json={"content": "写一份油菜菌核病防治方案"},
        headers=_hdr(world["owner"], world["yard"]),
    )
    assert res.status_code == 200, res.text
    payload = res.json()["data"]["assistant_message"]["payload"]
    assert payload["complete"] is False
    assert payload["missing_sections"]
    assert payload["needs_expert_review"] is True
    assert any("未写实" in w for w in payload["warnings"])


def test_plan_continues_truncated_output(client, db_session, world, monkeypatch):
    _stub_gather(monkeypatch)
    head = _draft(SECTION_NAMES[:6])
    loss = "收割前测产并记录含水量与机收损失率，损失率高于 4% 时调整割台高度与行进速度。"
    tail = "\n".join(f"## {name}\n{loss}" for name in SECTION_NAMES[6:])
    calls = _stub_llm(monkeypatch, [(head, "length"), tail])
    res = client.post(
        f"/api/v1/chat/sessions/{world['session'].id}/messages",
        json={"content": "写一份玉米机收减损方案"},
        headers=_hdr(world["owner"], world["yard"]),
    )
    assert res.status_code == 200, res.text
    assistant = res.json()["data"]["assistant_message"]
    assert len(calls) == 2
    assert assistant["payload"]["truncated"] is False
    assert "## 验收指标" in assistant["content"]
    assert calls[1].messages[0].content.startswith("继续写完上一段被截断的方案")


def test_plan_without_any_evidence_asks_expert_review(client, db_session, world, monkeypatch):
    _stub_gather(monkeypatch, {"knowledge": [], "web": []})
    _stub_llm(monkeypatch, [_draft()])
    res = client.post(
        f"/api/v1/chat/sessions/{world['session'].id}/messages",
        json={"content": "写一份小众作物栽培方案"},
        headers=_hdr(world["owner"], world["yard"]),
    )
    assert res.status_code == 200, res.text
    assistant = res.json()["data"]["assistant_message"]
    assert assistant["citations"] == []
    assert assistant["payload"]["needs_expert_review"] is True
    assert any("专家核实" in w for w in assistant["payload"]["warnings"])


def test_plan_long_answer_is_not_cut_by_input_cap(client, db_session, world, monkeypatch):
    _stub_gather(monkeypatch)
    long_body = "分蘖期保持浅水勤灌，并按叶色与田间水位记录判定复水与晒田时机。" * 40
    _stub_llm(monkeypatch, ["\n\n".join(f"## {name}\n{long_body}" for name in SECTION_NAMES)])
    res = client.post(
        f"/api/v1/chat/sessions/{world['session'].id}/messages",
        json={"content": "写一份水稻水分管理方案"},
        headers=_hdr(world["owner"], world["yard"]),
    )
    assert res.status_code == 200, res.text
    content = res.json()["data"]["assistant_message"]["content"]
    assert len(content) > 8000
    assert content.endswith("。")


def test_plan_skipped_when_assistant_is_named(client, db_session, world, monkeypatch):
    _stub_gather(monkeypatch)
    _stub_llm(monkeypatch, [_draft()])

    async def _ask_directed(db, *, ctx, question, target):
        return {
            "choice": {"kind": "agent", "id": "a1", "code": "ag", "name": "农事助手"},
            "routed_by": "bound",
            "reason": "定向执行",
            "result": {"kind": "agent", "answer": "定向回答", "status": "succeeded"},
        }

    from app.modules.agents import supervisor

    monkeypatch.setattr(supervisor, "ask_directed", _ask_directed)
    res = client.post(
        f"/api/v1/chat/sessions/{world['session'].id}/messages",
        json={"content": "写一份小麦赤霉病的防治方案", "target_code": "ag"},
        headers=_hdr(world["owner"], world["yard"]),
    )
    assert res.status_code == 200, res.text
    assert res.json()["data"]["assistant_message"]["routed_by"] == "bound"


def test_plan_history_reaches_prompt(client, db_session, world, monkeypatch):
    _stub_gather(monkeypatch)
    calls = _stub_llm(monkeypatch, [_draft(), _draft()])
    headers = _hdr(world["owner"], world["yard"])
    first = client.post(
        f"/api/v1/chat/sessions/{world['session'].id}/messages",
        json={"content": "写一份小麦赤霉病的防治方案"},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    second = client.post(
        f"/api/v1/chat/sessions/{world['session'].id}/messages",
        json={"content": "把上面的方案再补全一次"},
        headers=headers,
    )
    assert second.status_code == 200, second.text
    assert "<conversation_history>" in calls[-1].messages[1].content
    assert "小麦赤霉病" in calls[-1].messages[1].content


def test_collect_research_auto_skips_web_when_knowledge_is_enough(db_session, world, monkeypatch):
    class _Hit:
        document_id = "doc-1"
        chunk_id = "chunk-1"
        title = "资料"
        source = "seed"
        page_number = 1
        quote = "浅水勤灌"
        score = 0.7
        trust_level = "normal"

    monkeypatch.setattr(
        team_research, "knowledge_search", lambda db, ctx, q, top_k: [_Hit() for _ in range(3)]
    )
    monkeypatch.setattr(
        team_research,
        "has_permission",
        lambda *a, **k: True,
    )
    web_called = []

    async def _fake_search(db, yard_id, query, count=5, lang="zh-CN"):
        web_called.append(query)
        return {"results": []}

    monkeypatch.setattr(team_research, "search_web", _fake_search)

    evidence = asyncio.run(
        team_research.collect_research(
            db_session,
            ctx=_ctx(world),
            question="水稻分蘖期怎么管水",
            web="auto",
            web_min_kb_hits=3,
        )
    )
    assert len(evidence["knowledge"]) == 3
    assert evidence["web_skipped"] == "knowledge_enough"
    assert web_called == []


def test_collect_research_web_failure_degrades_to_warning(db_session, world, monkeypatch):
    monkeypatch.setattr(team_research, "knowledge_search", lambda db, ctx, q, top_k: [])
    monkeypatch.setattr(team_research, "has_permission", lambda *a, **k: True)

    async def _boom(db, yard_id, query, count=5, lang="zh-CN"):
        raise AppError("SEARCH_PROVIDER_UNAVAILABLE", "未配置搜索供应商。", http_status=503)

    monkeypatch.setattr(team_research, "search_web", _boom)
    evidence = asyncio.run(
        team_research.collect_research(db_session, ctx=_ctx(world), question="小麦赤霉病怎么防")
    )
    assert evidence["web"] == []
    assert any("SEARCH_PROVIDER_UNAVAILABLE" in w for w in evidence["warnings"])


def test_plan_without_any_content_fails_instead_of_persisting_empty_plan(
    client, db_session, world, monkeypatch
):
    """模型一个字都没给：按失败处理，不能落库一条空的「完整方案」。"""
    _stub_gather(monkeypatch)
    calls = _stub_llm(monkeypatch, ["", ""])
    res = client.post(
        f"/api/v1/chat/sessions/{world['session'].id}/messages",
        json={"content": "写一份小众作物栽培方案"},
        headers=_hdr(world["owner"], world["yard"]),
    )
    assert res.status_code == 502
    assert res.json()["error"]["code"] == "PLAN_EMPTY"
    assert len(calls) == 2


def test_plan_request_raises_its_own_timeout(client, db_session, world, monkeypatch):
    """整份方案要写得比全局 60 秒默认值久：请求自带超时，不被默认值截成超时。"""
    _stub_gather(monkeypatch)
    calls = _stub_llm(monkeypatch, [_draft()])
    res = client.post(
        f"/api/v1/chat/sessions/{world['session'].id}/messages",
        json={"content": "写一份小麦赤霉病的防治方案"},
        headers=_hdr(world["owner"], world["yard"]),
    )
    assert res.status_code == 200, res.text
    assert calls[0].timeout_seconds == plan_module.settings.plan_timeout_seconds
    assert calls[0].timeout_seconds > plan_module.settings.llm_timeout_seconds
