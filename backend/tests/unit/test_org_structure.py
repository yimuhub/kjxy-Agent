"""组织架构：树、岗位、Agent 绑定、循环与权限。"""

from __future__ import annotations

from app.core.deps import CurrentUser, get_current_user, get_db_session
from app.core.errors import (
    AppError,
    app_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.core.middleware import RequestIDMiddleware
from app.modules.agents.models import YardAgent
from app.modules.org.router import router as org_router
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient


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


def _agent(db, *, yard_id: str = "yard-org", code: str = "agronomist") -> YardAgent:
    row = YardAgent(
        yard_id=yard_id,
        agent_code=code,
        name="农艺师",
        persona_prompt="你是农艺师。",
        task_prompt_template="{{question}}",
    )
    db.add(row)
    db.commit()
    return row


def test_org_tree_positions_and_agent_binding(db_session):
    agent = _agent(db_session)
    client = _client(db_session)

    company = client.post(
        "/api/v1/org/units",
        json={"name": "某某农业合作社", "kind": "coop", "code": "coop-main"},
    )
    assert company.status_code == 200, company.text
    root = company.json()["data"]

    tech = client.post(
        "/api/v1/org/units",
        json={"name": "技术部", "parent_id": root["id"], "kind": "department"},
    )
    assert tech.status_code == 200
    tech_id = tech.json()["data"]["id"]

    position = client.post(
        "/api/v1/org/positions",
        json={
            "org_unit_id": tech_id,
            "title": "种植技术负责人",
            "responsibilities": "负责种植方案与病虫害处置",
            "headcount": 1,
            "agent_id": agent.id,
        },
    )
    assert position.status_code == 200, position.text
    assert position.json()["data"]["agent"]["name"] == "农艺师"

    tree = client.get("/api/v1/org/tree").json()["data"]
    assert tree["summary"] == {
        "units": 2,
        "departments": 1,
        "positions": 1,
        "agents_bound": 1,
        "roots": 1,
    }
    assert tree["items"][0]["name"] == "某某农业合作社"
    assert tree["items"][0]["children"][0]["name"] == "技术部"
    assert tree["items"][0]["children"][0]["positions"][0]["title"] == "种植技术负责人"
    assert [row["depth"] for row in tree["flat"]] == [0, 1]
    assert tree["flat"][0]["path"] == "某某农业合作社"
    assert tree["flat"][1]["path"] == "某某农业合作社 / 技术部"


def test_org_rejects_cycle_cross_yard_parent_and_duplicate_code(db_session):
    client = _client(db_session)
    root = client.post("/api/v1/org/units", json={"name": "总部", "code": "hq"}).json()["data"]
    child = client.post(
        "/api/v1/org/units", json={"name": "分部", "parent_id": root["id"], "code": "branch"}
    ).json()["data"]

    cycle = client.put(f"/api/v1/org/units/{root['id']}", json={"parent_id": child["id"]})
    assert cycle.status_code == 400
    assert cycle.json()["error"]["code"] == "ORG_CYCLE"

    duplicate = client.post("/api/v1/org/units", json={"name": "另一个总部", "code": "hq"})
    assert duplicate.status_code == 409

    other = _client(db_session, yard_id="yard-other")
    foreign = other.post("/api/v1/org/units", json={"name": "外部单元", "code": "external"})
    foreign_id = foreign.json()["data"]["id"]
    cross = client.post(
        "/api/v1/org/units",
        json={"name": "非法挂靠", "parent_id": foreign_id},
    )
    assert cross.status_code == 404

    # 跨院不可读、不可删
    assert client.get(f"/api/v1/org/units/{foreign_id}").status_code == 404
    assert client.delete(f"/api/v1/org/units/{foreign_id}").status_code == 404


def test_org_delete_guard_and_archive(db_session):
    client = _client(db_session)
    root = client.post("/api/v1/org/units", json={"name": "总部", "code": "hq"}).json()["data"]
    client.post("/api/v1/org/units", json={"name": "分部", "parent_id": root["id"]})

    blocked = client.delete(f"/api/v1/org/units/{root['id']}")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "ORG_UNIT_NOT_EMPTY"

    archived = client.put(f"/api/v1/org/units/{root['id']}", json={"status": "archived"})
    assert archived.status_code == 200
    assert client.get("/api/v1/org/tree").json()["data"]["summary"]["units"] == 1
    full = client.get("/api/v1/org/tree?include_archived=true").json()["data"]
    assert full["summary"]["units"] == 2


def test_org_permissions_and_position_agent_validation(db_session):
    reader = _client(db_session, roles=["viewer"])
    assert reader.get("/api/v1/org/tree").status_code == 200
    denied = reader.post("/api/v1/org/units", json={"name": "越权新建"})
    assert denied.status_code == 403

    nobody = _client(db_session, roles=["service_worker"])
    assert nobody.get("/api/v1/org/tree").status_code == 403

    client = _client(db_session)
    root = client.post("/api/v1/org/units", json={"name": "总部"}).json()["data"]
    foreign_agent = _agent(db_session, yard_id="yard-other", code="external-agent")
    bad = client.post(
        "/api/v1/org/positions",
        json={"org_unit_id": root["id"], "title": "外部岗位", "agent_id": foreign_agent.id},
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "ORG_AGENT_INVALID"

    missing_unit = client.post(
        "/api/v1/org/positions",
        json={"org_unit_id": "not-exist", "title": "孤儿岗位"},
    )
    assert missing_unit.status_code == 404


def test_org_position_update_and_delete(db_session):
    client = _client(db_session)
    root = client.post("/api/v1/org/units", json={"name": "总部"}).json()["data"]
    position = client.post(
        "/api/v1/org/positions",
        json={"org_unit_id": root["id"], "title": "负责人", "headcount": 2},
    ).json()["data"]

    updated = client.put(
        f"/api/v1/org/positions/{position['id']}",
        json={"title": "理事长", "responsibilities": "统筹合作社经营", "headcount": 1},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["title"] == "理事长"
    assert updated.json()["data"]["responsibilities"] == "统筹合作社经营"

    deleted = client.delete(f"/api/v1/org/positions/{position['id']}")
    assert deleted.status_code == 200
    assert client.get(f"/api/v1/org/units/{root['id']}").json()["data"]["positions"] == []
    assert client.delete(f"/api/v1/org/positions/{position['id']}").status_code == 404
