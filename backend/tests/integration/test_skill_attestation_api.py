"""技能验签与评测报告 HTTP 接口（v3.17）。"""

from __future__ import annotations

import pytest
from app.core.deps import YardContext
from app.core.security import create_access_token
from app.modules.auth.models import User
from app.modules.evolution import service
from app.modules.yards.repository import add_membership, create_yard

PREFIX = "/api/v1"

_GOOD = {
    "name": "番茄灰霉病识别",
    "description": "根据描述判断番茄灰霉病。",
    "prompt": "你是植保助手。请依据知识库证据给出判断与处置建议，资料不足时明确说明。",
    "steps": ["检索知识库", "归纳证据"],
    "allowed_tools": [],
    "sources": [{"title": "番茄病害手册"}],
    "guardrails": {"risk_level": "low", "require_citation": True},
    "license": "CC-BY-4.0",
}


def _user(db, username: str) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash="x",
        display_name=username,
        status="active",
        token_version=0,
    )
    db.add(user)
    db.flush()
    return user


def _hdr(user: User, yard_id: str, role: str) -> dict[str, str]:
    token = create_access_token(user_id=user.id, yard_id=yard_id, roles=[role])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def world(db_session):
    yard = create_yard(db_session, name="验签测试小院")
    expert = _user(db_session, "att_expert")
    viewer = _user(db_session, "att_viewer")
    add_membership(db_session, yard_id=yard.id, user_id=expert.id, role="expert")
    add_membership(db_session, yard_id=yard.id, user_id=viewer.id, role="viewer")
    db_session.flush()
    return {"yard": yard, "expert": expert, "viewer": viewer}


def _published_skill(db, world):
    ctx = YardContext(
        yard_id=world["yard"].id,
        user_id=world["expert"].id,
        roles=["expert"],
        request_id="req_att_api",
    )
    skill = service.create_skill(
        db, ctx, skill_code="att_tomato", version="1.0.0", definition=_GOOD
    )
    service.review_skill(db, ctx, skill.id, "submit", None)
    service.review_skill(db, ctx, skill.id, "submit", None)
    service.review_skill(db, ctx, skill.id, "approve", None)
    db.commit()
    db.refresh(skill)
    return skill


def test_signing_key_is_public_and_stable(client, world):
    headers = _hdr(world["viewer"], world["yard"].id, "viewer")
    first = client.get(f"{PREFIX}/skills/signing-key", headers=headers)
    second = client.get(f"{PREFIX}/skills/signing-key", headers=headers)
    assert first.status_code == 200
    data = first.json()["data"]
    assert data["alg"] == "ed25519"
    assert data["public_key"] and data["key_id"]
    assert data == second.json()["data"]


def test_attestation_and_report_endpoints(client, db_session, world):
    skill = _published_skill(db_session, world)
    headers = _hdr(world["viewer"], world["yard"].id, "viewer")

    resp = client.get(f"{PREFIX}/skills/{skill.id}/attestation", headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["verification"]["valid"] is True
    assert data["verification"]["reason"] == "ok"
    assert data["signed_by"] == world["expert"].id
    assert data["latest_eval_run"]["kind"] == "static"
    report = data["latest_eval_run"]["report"]
    assert report["pass"] is True
    assert any(c["code"] == "identity" for c in report["checks"])

    run_id = data["latest_eval_run"]["id"]
    detail = client.get(f"{PREFIX}/skills/{skill.id}/eval-runs/{run_id}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()["data"]
    assert body["report_sha256"] == data["latest_eval_run"]["report_sha256"]
    assert body["report"]["score"] == report["score"]
    assert body["kind"] == "static"

    # 评测历史列表带摘要字段，不含全文
    runs = client.get(f"{PREFIX}/skills/{skill.id}/eval-runs", headers=headers).json()
    assert runs["meta"]["total"] == 1
    assert runs["data"][0]["kind"] == "static"
    assert "report" not in runs["data"][0]


def test_attestation_flags_tampering(client, db_session, world):
    skill = _published_skill(db_session, world)
    skill.definition_json = dict(skill.definition_json, prompt="被替换的提示词，长度足够长。")
    db_session.flush()
    db_session.commit()

    headers = _hdr(world["viewer"], world["yard"].id, "viewer")
    data = client.get(f"{PREFIX}/skills/{skill.id}/attestation", headers=headers).json()["data"]
    assert data["verification"]["valid"] is False
    assert data["verification"]["reason"] == "definition_mismatch"


def test_eval_run_not_found_for_other_version(client, db_session, world):
    skill = _published_skill(db_session, world)
    headers = _hdr(world["viewer"], world["yard"].id, "viewer")
    resp = client.get(f"{PREFIX}/skills/{skill.id}/eval-runs/no-such-run", headers=headers)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "EVAL_RUN_NOT_FOUND"
