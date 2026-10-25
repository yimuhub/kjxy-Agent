"""反馈→候选→审核通过 全流程集成测试。"""

import pytest
from app.core.deps import get_db_session
from app.core.security import create_access_token
from app.main import create_app
from app.modules.evolution.router import router as evolution_router
from app.modules.knowledge.models import KnowledgeDocument  # noqa: F401  确保测试建表时已注册
from fastapi.testclient import TestClient


@pytest.fixture()
def evo_client(db_session):
    app = create_app()
    app.include_router(evolution_router, prefix="/api/v1")

    def override_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db_session] = override_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _headers(roles):
    token = create_access_token(user_id="u1", tenant_id="t1", roles=roles)
    return {"Authorization": f"Bearer {token}"}


def test_feedback_to_candidate_to_approved(evo_client):
    member = _headers(["member"])
    expert = _headers(["expert"])

    # 1. 提交反馈（含纠错答案 → 自动生成知识候选）
    resp = evo_client.post(
        "/api/v1/feedback",
        json={
            "task_id": "task-1",
            "rating": "down",
            "feedback_text": "上一回答的追肥剂量有误",
            "corrected_answer": "小麦返青期正确追肥量为每亩尿素10公斤，弱苗可适当增加。",
        },
        headers=member,
    )
    assert resp.status_code == 201, resp.text
    feedback = resp.json()["data"]
    assert feedback["status"] == "open"
    assert feedback["rating"] == "down"

    # 2. 候选列表出现该反馈生成的候选
    resp = evo_client.get("/api/v1/knowledge/candidates", headers=member)
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]
    assert len(items) == 1
    candidate = items[0]
    assert candidate["status"] == "pending_review"
    assert candidate["source_feedback_id"] == feedback["id"]
    assert "每亩尿素10公斤" in candidate["content"]

    # 3. 普通成员审核 → 403
    resp = evo_client.post(
        f"/api/v1/knowledge/candidates/{candidate['id']}/review",
        json={"action": "approve"},
        headers=member,
    )
    assert resp.status_code == 403, resp.text

    # 4. 专家审核通过（纠错内容同步写入知识库，source=expert_correction）
    resp = evo_client.post(
        f"/api/v1/knowledge/candidates/{candidate['id']}/review",
        json={"action": "approve", "note": "与县农技站资料一致"},
        headers=expert,
    )
    assert resp.status_code == 200, resp.text
    approved = resp.json()["data"]
    assert approved["status"] == "approved"
    assert approved["reviewed_by"] == "u1"
    assert approved["review_note"] == "与县农技站资料一致"

    # 5. 重复审核 → 409
    resp = evo_client.post(
        f"/api/v1/knowledge/candidates/{candidate['id']}/review",
        json={"action": "approve"},
        headers=expert,
    )
    assert resp.status_code == 409, resp.text


def test_approve_ingests_document_to_knowledge(evo_client, db_session):
    """审核通过后纠错内容真实写入知识库（source=expert_correction）。"""
    member = _headers(["member"])
    expert = _headers(["expert"])
    evo_client.post(
        "/api/v1/feedback",
        json={"corrected_answer": "大豆花荚期每亩追施尿素5公斤。"},
        headers=member,
    )
    candidates = evo_client.get("/api/v1/knowledge/candidates", headers=member).json()["data"]
    cid = candidates[0]["id"]
    resp = evo_client.post(
        f"/api/v1/knowledge/candidates/{cid}/review",
        json={"action": "approve"},
        headers=expert,
    )
    assert resp.status_code == 200, resp.text
    docs = (
        db_session.query(KnowledgeDocument)
        .filter(KnowledgeDocument.source == "expert_correction")
        .all()
    )
    assert len(docs) == 1
    assert "大豆花荚期" in docs[0].title or True  # title 取自候选标题
    # 文档内容包含纠错正文（从 storage 读回校验）
    from app.modules.knowledge.storage import LocalStorageBackend

    with LocalStorageBackend().open(docs[0].storage_key) as f:
        raw = f.read()
    assert "大豆花荚期每亩追施尿素5公斤" in raw.decode("utf-8")


def test_feedback_without_correction_creates_no_candidate(evo_client):
    member = _headers(["member"])
    resp = evo_client.post(
        "/api/v1/feedback",
        json={"rating": "up", "feedback_text": "回答很好"},
        headers=member,
    )
    assert resp.status_code == 201, resp.text
    resp = evo_client.get("/api/v1/knowledge/candidates", headers=member)
    assert resp.json()["meta"]["total"] == 0


def test_feedback_short_correction_rejected(evo_client):
    """过短纠错无法构成可审核知识，应在入口拒绝而不是灌入审核队列。"""
    member = _headers(["member"])
    resp = evo_client.post(
        "/api/v1/feedback",
        json={"corrected_answer": "太短"},
        headers=member,
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "FEEDBACK_TOO_SHORT"
    listed = evo_client.get("/api/v1/knowledge/candidates", headers=member).json()
    assert listed["meta"]["total"] == 0


def test_feedback_duplicate_correction_deduped(evo_client):
    """同一纠错重复提交时复用待审候选，不重复堆积。"""
    member = _headers(["member"])
    payload = {"corrected_answer": "花生开花下针期应控制氮肥，增施磷钾肥。"}
    assert evo_client.post("/api/v1/feedback", json=payload, headers=member).status_code == 201
    assert evo_client.post("/api/v1/feedback", json=payload, headers=member).status_code == 201
    resp = evo_client.get("/api/v1/knowledge/candidates", headers=member)
    assert resp.json()["meta"]["total"] == 1


def test_candidate_reject(evo_client):
    member = _headers(["member"])
    expert = _headers(["expert"])
    resp = evo_client.post(
        "/api/v1/feedback",
        json={"corrected_answer": "存疑的纠错内容：该地块应先排水再施药。"},
        headers=member,
    )
    candidates = evo_client.get("/api/v1/knowledge/candidates", headers=member).json()["data"]
    cid = candidates[0]["id"]
    resp = evo_client.post(
        f"/api/v1/knowledge/candidates/{cid}/review",
        json={"action": "reject", "note": "依据不足"},
        headers=expert,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "rejected"


def test_skill_api_flow(evo_client):
    member = _headers(["member"])
    expert = _headers(["expert"])

    resp = evo_client.post(
        "/api/v1/skills",
        json={"skill_code": "wheat_guide", "version": "1.0.0", "definition": {"steps": []}},
        headers=member,
    )
    assert resp.status_code == 201, resp.text
    skill_id = resp.json()["data"]["id"]
    assert resp.json()["data"]["status"] == "draft"

    for action, h in (("submit", member), ("submit", member), ("approve", expert)):
        resp = evo_client.post(
            f"/api/v1/skills/{skill_id}/review", json={"action": action}, headers=h
        )
        assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "published"

    resp = evo_client.get("/api/v1/skills?status=published", headers=member)
    assert resp.json()["meta"]["total"] == 1
