"""指挥官 REST 集成：运行详情/执行/重试/取消的权限边界 + 设置读写。"""

from __future__ import annotations

import pytest
from app.core.security import create_access_token
from app.modules.audit.models import AuditLog
from app.modules.commander import service as commander_service
from app.modules.commander.models import CommanderRun, CommanderSubtask
from app.modules.yards.repository import create_yard

PREFIX = "/api/v1"


def _hdr(user_id: str, yard_id: str, roles: list[str]) -> dict[str, str]:
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=roles)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def yard(db_session):
    row = create_yard(db_session, name="指挥官 API 小院")
    row.owner_user_id = "u-owner"
    db_session.commit()
    return row


@pytest.fixture()
def owner_hdr(yard):
    return _hdr("u-owner", yard.id, ["yard_owner"])


@pytest.fixture()
def creator_hdr(yard):
    return _hdr("u-creator", yard.id, ["yard_admin"])


@pytest.fixture()
def other_hdr(yard):
    return _hdr("u-other", yard.id, ["viewer"])


def _make_run(db, yard, *, status: str = "queued", sub_status: str = "pending"):
    run = CommanderRun(
        yard_id=yard.id,
        session_id="s-api",
        user_id="u-creator",
        goal="做一份提案",
        plan_json={"fallback": False, "subtasks": []},
        status=status,
        max_parallel=3,
        effective_parallel=3,
        degraded=False,
        tokens_total=0,
        roles_json=["yard_admin"],
    )
    db.add(run)
    db.flush()
    db.add(
        CommanderSubtask(
            run_id=run.id,
            yard_id=yard.id,
            seq=1,
            title="起草大纲",
            instruction="起草大纲",
            expected_output="markdown",
            agent_source="commander",
            agent_name="指挥官",
            status=sub_status,
            depends_on_json=[],
            attempts=1 if sub_status == "error" else 0,
            error_code="COMMANDER_SUBTASK_ERROR" if sub_status == "error" else None,
        )
    )
    db.commit()
    return run


def test_get_run_shape_and_owner_boundary(
    client, db_session, yard, creator_hdr, owner_hdr, other_hdr
):
    run = _make_run(db_session, yard)
    url = f"{PREFIX}/commander/runs/{run.id}"

    resp = client.get(url, headers=creator_hdr)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]["run"]
    assert data["id"] == run.id
    assert data["status"] == "queued"
    assert data["plan_fallback"] is False
    assert data["subtasks"][0]["title"] == "起草大纲"
    assert data["subtasks"][0]["tokens"] == 0

    assert client.get(url, headers=owner_hdr).status_code == 200

    denied = client.get(url, headers=other_hdr)
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "COMMANDER_RUN_NOT_FOUND"


def test_execute_returns_202_and_schedules(client, db_session, yard, creator_hdr, monkeypatch):
    run = _make_run(db_session, yard)
    called: list[str] = []

    async def fake_background(run_id: str) -> None:
        called.append(run_id)

    monkeypatch.setattr(commander_service, "run_in_background", fake_background)
    resp = client.post(f"{PREFIX}/commander/runs/{run.id}/execute", headers=creator_hdr)
    assert resp.status_code == 202, resp.text
    body = resp.json()["data"]
    assert body == {"run_id": run.id, "status": "queued"}


def test_execute_other_users_run_is_404(client, db_session, yard, other_hdr, monkeypatch):
    run = _make_run(db_session, yard)

    async def fake_background(run_id: str) -> None:  # pragma: no cover - 不应被调用
        raise AssertionError("越权请求不得启动执行")

    monkeypatch.setattr(commander_service, "run_in_background", fake_background)
    resp = client.post(f"{PREFIX}/commander/runs/{run.id}/execute", headers=other_hdr)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "COMMANDER_RUN_NOT_FOUND"


def test_retry_endpoint_resets_failed_subtask(client, db_session, yard, creator_hdr):
    run = _make_run(db_session, yard, status="partial", sub_status="error")
    resp = client.post(f"{PREFIX}/commander/runs/{run.id}/retry", headers=creator_hdr)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]["run"]
    assert data["status"] == "queued"
    assert data["subtasks"][0]["status"] == "pending"
    assert data["subtasks"][0]["attempts"] == 1


def test_cancel_endpoint_skips_pending(client, db_session, yard, creator_hdr):
    run = _make_run(db_session, yard)
    resp = client.post(f"{PREFIX}/commander/runs/{run.id}/cancel", headers=creator_hdr)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]["run"]
    assert data["status"] == "cancelled"
    assert data["subtasks"][0]["status"] == "skipped"


def test_cancel_other_users_run_is_404(client, db_session, yard, other_hdr):
    run = _make_run(db_session, yard)
    resp = client.post(f"{PREFIX}/commander/runs/{run.id}/cancel", headers=other_hdr)
    assert resp.status_code == 404


def test_unknown_run_is_404(client, yard, creator_hdr):
    resp = client.get(f"{PREFIX}/commander/runs/not-exists", headers=creator_hdr)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "COMMANDER_RUN_NOT_FOUND"


def test_settings_get_returns_defaults(client, yard, owner_hdr):
    resp = client.get(f"{PREFIX}/yards/current/settings/commander", headers=owner_hdr)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["enabled"] is True
    assert data["display_name"] == "指挥官"
    assert data["max_parallel"] == 3
    assert data["fallback_parallel"] == 2
    assert data["auto_publish_agent"] is False


def test_settings_partial_put_and_invalid_value(client, yard, owner_hdr):
    resp = client.put(
        f"{PREFIX}/yards/current/settings/commander",
        json={"max_parallel": 5, "display_name": "总指挥"},
        headers=owner_hdr,
    )
    assert resp.status_code == 200, resp.text
    # 既有 PUT 语义：回包是全部设置，指挥官配置在 commander 键下
    data = resp.json()["data"]["commander"]
    assert data["max_parallel"] == 5
    assert data["display_name"] == "总指挥"
    assert data["fallback_parallel"] == 2

    bad = client.put(
        f"{PREFIX}/yards/current/settings/commander",
        json={"max_parallel": 99},
        headers=owner_hdr,
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "INVALID_REQUEST"


def test_auto_publish_requires_owner_and_writes_audit(
    client, db_session, yard, creator_hdr, owner_hdr
):
    denied = client.put(
        f"{PREFIX}/yards/current/settings/commander",
        json={"auto_publish_agent": True},
        headers=creator_hdr,
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "PROTECTED_CONFIRM_REQUIRED"

    allowed = client.put(
        f"{PREFIX}/yards/current/settings/commander",
        json={"auto_publish_agent": True},
        headers=owner_hdr,
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["data"]["commander"]["auto_publish_agent"] is True

    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "commander_auto_publish_enabled")
        .one_or_none()
    )
    assert audit is not None
    assert audit.actor_id == "u-owner"


def test_settings_require_login(client, yard):
    assert client.get(f"{PREFIX}/yards/current/settings/commander").status_code == 401


def test_capability_codes_registered():
    from app.modules.llm.capabilities import VALID_PURPOSES, capability_payload

    assert "commander_plan" in VALID_PURPOSES
    assert "commander_merge" in VALID_PURPOSES
    labels = {item["code"]: item for item in capability_payload()}
    assert labels["commander_plan"]["group"] == "智能体编排"
    assert labels["commander_merge"]["group"] == "智能体编排"


def test_stats_endpoint_returns_zero_shape(client, yard, creator_hdr):
    resp = client.get(f"{PREFIX}/commander/stats?days=7", headers=creator_hdr)
    assert resp.status_code == 200, resp.text
    stats = resp.json()["data"]["stats"]
    assert stats["window_days"] == 7
    assert stats["runs"]["total"] == 0
    assert stats["subtasks"]["success_rate"] == 0.0
    assert stats["latency_ms"]["p90"] == 0


def test_preview_endpoint_plans_without_executing(
    client, db_session, yard, creator_hdr, monkeypatch
):
    from types import SimpleNamespace

    run = SimpleNamespace(id="run-preview", tokens_total=5, status="queued", plan_json={})

    async def fake_create_plan(db, *, ctx, session_id, user_content, context=""):
        assert session_id == "s-preview"
        assert user_content == "做一份春耕方案"
        return run

    def fake_get_run(db, yard_id, run_id):
        return run, []

    def fake_run_out(run_obj, subtasks):
        return {"id": run_obj.id, "status": "queued", "clarify": [], "subtasks": []}

    monkeypatch.setattr(commander_service, "create_plan_run", fake_create_plan)
    monkeypatch.setattr(commander_service, "get_run", fake_get_run)
    monkeypatch.setattr(commander_service, "run_out", fake_run_out)

    resp = client.post(
        f"{PREFIX}/commander/preview",
        json={"content": "做一份春耕方案", "session_id": "s-preview"},
        headers=creator_hdr,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["run"]["id"] == "run-preview"
    assert data["clarify"] == []


def test_preview_rejects_unknown_field(client, yard, creator_hdr):
    resp = client.post(
        f"{PREFIX}/commander/preview",
        json={"content": "做方案", "session_id": "s-1", "execute": True},
        headers=creator_hdr,
    )
    assert resp.status_code == 400


def test_edit_plan_replaces_subtasks_for_queued_run(client, db_session, yard, creator_hdr):
    run = _make_run(db_session, yard)
    resp = client.put(
        f"{PREFIX}/commander/runs/{run.id}/plan",
        json={
            "subtasks": [
                {"title": "先调研", "instruction": "调研市场", "agent_id": None, "depends_on": []},
                {
                    "title": "再写稿",
                    "instruction": "基于调研写稿",
                    "agent_id": None,
                    "depends_on": [1],
                },
            ]
        },
        headers=creator_hdr,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]["run"]
    assert [row["seq"] for row in data["subtasks"]] == [1, 2]
    assert data["subtasks"][0]["title"] == "先调研"
    assert data["subtasks"][1]["depends_on"] == [1]
    assert data["subtasks"][0]["agent_source"] == "commander"


def test_edit_plan_rejects_started_run(client, db_session, yard, creator_hdr):
    run = _make_run(db_session, yard, status="running")
    resp = client.put(
        f"{PREFIX}/commander/runs/{run.id}/plan",
        json={"subtasks": [{"title": "改", "instruction": "改", "agent_id": None}]},
        headers=creator_hdr,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "COMMANDER_RUN_STATE"


def test_edit_plan_rejects_invisible_private_agent(client, db_session, yard, creator_hdr):
    from app.modules.agents.models import YardAgent

    run = _make_run(db_session, yard)
    other = YardAgent(
        yard_id=yard.id,
        agent_code="private_other",
        name="别人的私有助手",
        persona_prompt="你是助手。",
        task_prompt_template="回答：{{question}}",
        status="published",
        visibility="private",
        created_by="u-someone-else",
        created_from="manual",
    )
    db_session.add(other)
    db_session.commit()
    resp = client.put(
        f"{PREFIX}/commander/runs/{run.id}/plan",
        json={
            "subtasks": [
                {"title": "问", "instruction": "问", "agent_id": other.id},
            ]
        },
        headers=creator_hdr,
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_REQUEST"
