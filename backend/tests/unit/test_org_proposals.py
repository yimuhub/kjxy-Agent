"""知识库推导组织架构：归一化、差异预览、应用与权限。"""

from __future__ import annotations

import asyncio
import json

import pytest
from app.core.deps import CurrentUser, YardContext, get_current_user, get_db_session
from app.core.errors import (
    AppError,
    app_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.core.middleware import RequestIDMiddleware
from app.modules.llm.providers_fake import (
    reset_scripted_tool_responses,
    set_scripted_tool_responses,
)
from app.modules.org import proposal_service
from app.modules.org import service as org_service
from app.modules.org.models import (
    PROPOSAL_STATUS_APPLIED,
    PROPOSAL_STATUS_GENERATING,
    PROPOSAL_STATUS_READY,
    OrgProposal,
)
from app.modules.org.router import router as org_router
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient


def _ctx(roles: list[str] | None = None) -> YardContext:
    return YardContext(
        yard_id="yard-org",
        user_id="u-org",
        roles=roles if roles is not None else ["yard_admin"],
        request_id="req-org",
    )


def _client(db, roles: list[str] | None = None, yard_id: str = "yard-org") -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
    app.include_router(org_router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="u-org",
        yard_id=yard_id,
        roles=roles if roles is not None else ["yard_admin"],
        request_id="req-org",
    )
    return TestClient(app)


def _proposal(db, *, status: str = PROPOSAL_STATUS_GENERATING, payload: dict | None = None):
    row = OrgProposal(
        yard_id="yard-org",
        title=(payload or {}).get("title", ""),
        instruction="",
        status=status,
        proposal_json=payload,
        created_by="u-org",
    )
    db.add(row)
    db.commit()
    return row


def _sample_payload() -> dict:
    return {
        "title": "合作社组织建议",
        "summary": "依据章程与岗位说明推导。",
        "units": [
            {
                "name": "理事会",
                "kind": "coop",
                "description": "决策机构",
                "parent_name": None,
                "positions": [{"title": "理事长", "responsibilities": "统筹", "headcount": 1}],
                "agent_suggestion": {
                    "name": "理事长助理",
                    "mission": "跟踪决议执行",
                    "reason": "章程要求定期复盘",
                },
            },
            {
                "name": "技术部",
                "kind": "department",
                "description": "种植技术",
                "parent_name": "理事会",
                "positions": [{"title": "农艺师", "responsibilities": "方案", "headcount": 2}],
                "agent_suggestion": None,
            },
        ],
        "agent_suggestions": [],
    }


def test_generate_normalizes_and_keeps_evidence(db_session, monkeypatch):
    proposal = _proposal(db_session)
    evidence = [
        {
            "chunk_id": "c1",
            "document_id": "d1",
            "title": "合作社章程",
            "page_number": 2,
            "quote": "技术部负责种植方案。",
            "score": 0.9,
        }
    ]
    monkeypatch.setattr(proposal_service, "_retrieve", lambda *_args, **_kw: evidence)
    content = json.dumps(
        {
            "title": "技术驱动型合作社",
            "summary": "依据章程。",
            "units": [
                {
                    "name": "技术部",
                    "kind": "department",
                    "parent_name": "理事会",
                    "positions": [{"title": "农艺师", "headcount": 2}],
                    "evidence_ids": ["c1"],
                },
                {
                    "name": "理事会",
                    "kind": "coop",
                    "parent_name": None,
                    "positions": [{"title": "理事长"}],
                },
                {"name": "外部挂靠", "parent_name": "不存在的父级"},
            ],
        }
    )
    set_scripted_tool_responses([{"content": content}])
    try:
        summary = asyncio.run(
            proposal_service.generate(
                db_session,
                ctx=_ctx(),
                proposal_id=proposal.id,
                instruction="按章程设计",
            )
        )
    finally:
        reset_scripted_tool_responses()

    db_session.refresh(proposal)
    assert summary["units"] == 3
    assert proposal.status == PROPOSAL_STATUS_READY
    assert proposal.evidence_json == evidence
    units = proposal.proposal_json["units"]  # type: ignore[index]
    assert units[0]["name"] == "理事会"  # 父单元在前
    assert units[1]["name"] == "技术部"
    assert units[1]["parent_name"] == "理事会"
    assert units[2]["parent_name"] is None  # 未知父级降为顶层


def test_generate_requires_knowledge(db_session, monkeypatch):
    proposal = _proposal(db_session)
    monkeypatch.setattr(proposal_service, "_retrieve", lambda *_args, **_kw: [])
    with pytest.raises(AppError) as exc:
        asyncio.run(
            proposal_service.generate(
                db_session, ctx=_ctx(), proposal_id=proposal.id, instruction=""
            )
        )
    assert exc.value.code == "ORG_KNOWLEDGE_EMPTY"


def test_generate_invalid_json_retries_then_fails(db_session, monkeypatch):
    proposal = _proposal(db_session)
    monkeypatch.setattr(proposal_service, "_retrieve", lambda *_args, **_kw: [_evidence()])
    set_scripted_tool_responses([{"content": "不是 JSON"}, {"content": "仍不是 JSON"}])
    try:
        with pytest.raises(AppError) as exc:
            asyncio.run(
                proposal_service.generate(
                    db_session, ctx=_ctx(), proposal_id=proposal.id, instruction=""
                )
            )
    finally:
        reset_scripted_tool_responses()
    assert exc.value.code == "ORG_PROPOSAL_INVALID"


def _evidence() -> dict:
    return {
        "chunk_id": "c1",
        "document_id": "d1",
        "title": "章程",
        "page_number": None,
        "quote": "依据",
        "score": 0.5,
    }


def test_plan_and_apply_merge_are_idempotent(db_session):
    db_session.commit()
    root = org_service.create_unit(
        db_session,
        yard_id="yard-org",
        actor_id="u-org",
        name="理事会",
        parent_id=None,
        code="council",
        kind="coop",
        description="",
        sort_order=0,
    )
    tech = org_service.create_unit(
        db_session,
        yard_id="yard-org",
        actor_id="u-org",
        name="技术部",
        parent_id=root.id,
        code="tech",
        kind="department",
        description="",
        sort_order=0,
    )
    org_service.create_position(
        db_session,
        yard_id="yard-org",
        actor_id="u-org",
        org_unit_id=tech.id,
        title="农艺师",
        responsibilities="",
        headcount=1,
        agent_id=None,
        sort_order=0,
    )
    db_session.commit()

    payload = _sample_payload()
    payload["units"].append(
        {
            "name": "市场部",
            "kind": "department",
            "parent_name": "理事会",
            "positions": [{"title": "渠道经理", "responsibilities": "渠道", "headcount": 1}],
            "agent_suggestion": None,
        }
    )
    proposal = _proposal(db_session, status=PROPOSAL_STATUS_READY, payload=payload)

    plan = proposal_service.build_plan(db_session, "yard-org", payload, parent_id=None)
    assert plan["counts"] == {
        "create_units": 1,
        "existing_units": 2,
        "create_positions": 2,
        "existing_positions": 1,
    }

    result = proposal_service.apply_proposal(
        db_session, ctx=_ctx(), proposal_id=proposal.id, parent_id=None
    )
    assert result["already_applied"] is False
    assert result["result"] == {
        "created_units": 1,
        "merged_units": 2,
        "created_positions": 2,
        "merged_positions": 1,
    }
    db_session.refresh(proposal)
    assert proposal.status == PROPOSAL_STATUS_APPLIED

    again = proposal_service.apply_proposal(
        db_session, ctx=_ctx(), proposal_id=proposal.id, parent_id=None
    )
    assert again["already_applied"] is True


def test_stale_generating_proposal_heals_from_failed_task(db_session):
    from app.modules.tasks import state_machine as sm
    from app.modules.tasks.models import Task

    proposal = _proposal(db_session)
    task = Task(
        yard_id="yard-org",
        created_by="u-org",
        task_type=proposal_service.TASK_TYPE,
        status=sm.STATUS_FAILED,
        input_json={},
        error_message="模型调用超时",
    )
    db_session.add(task)
    db_session.flush()
    proposal.task_id = task.id
    db_session.flush()

    healed = proposal_service.get_proposal(db_session, "yard-org", proposal.id)
    assert healed.status == "failed"
    assert "模型调用超时" in (healed.error_message or "")


def test_proposal_api_summary_apply_and_permissions(db_session):
    payload = _sample_payload()
    proposal = _proposal(db_session, status=PROPOSAL_STATUS_READY, payload=payload)

    admin = _client(db_session)
    listed = admin.get("/api/v1/org/proposals")
    assert listed.status_code == 200
    assert listed.json()["data"][0]["units"] == 2

    detail = admin.get(f"/api/v1/org/proposals/{proposal.id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["diff"]["counts"]["create_units"] == 2

    applied = admin.post(f"/api/v1/org/proposals/{proposal.id}/apply", json={})
    assert applied.status_code == 200
    assert applied.json()["data"]["result"]["created_units"] == 2

    again = admin.post(f"/api/v1/org/proposals/{proposal.id}/apply", json={})
    assert again.status_code == 200
    assert again.json()["data"]["already_applied"] is True

    discard = admin.post(f"/api/v1/org/proposals/{proposal.id}/discard")
    assert discard.status_code == 409

    viewer = _client(db_session, roles=["viewer"])
    assert viewer.get("/api/v1/org/proposals").status_code == 200
    assert viewer.post("/api/v1/org/proposals", json={"instruction": "x"}).status_code == 403

    nobody = _client(db_session, roles=["service_worker"])
    assert nobody.get("/api/v1/org/proposals").status_code == 403
