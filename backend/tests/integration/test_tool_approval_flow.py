"""高风险工具审批流集成测试（第一期 1.3）。

链路：创建 workorder.dispatch 任务 → 工具被风险拦截 → 审批单 pending +
任务 awaiting_review → 专家批准 → 工具执行 → 任务 succeeded；
驳回分支：任务 cancelled。
"""

import pytest
from app.core.config import settings
from app.core.deps import get_db_session
from app.core.security import create_access_token, hash_password
from app.db.base import Base
from app.main import create_app
from app.modules.auth.models import User
from app.modules.tasks import state_machine as sm
from app.modules.tasks import worker_tasks
from app.modules.tasks.models import Task
from app.modules.tasks.router import router as tasks_router
from app.modules.tools.approvals_router import router as approvals_router
from app.modules.tools.models import ToolApproval
from app.modules.tools.router import router as tools_router
from app.modules.tools.workorder_tool import TASK_TYPE, register_workorder_tool
from app.modules.yards.models import Yard, YardMembership
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

YARD_ID = "yard-approval-1"
ADMIN_ID = "user-approval-admin"
EXPERT_ID = "user-approval-expert"


def _token(user_id: str, roles: list[str]) -> str:
    return create_access_token(user_id=user_id, yard_id=YARD_ID, roles=roles)


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=eng)
    yield eng
    Base.metadata.drop_all(bind=eng)


@pytest.fixture()
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture()
def seed_data(session_factory):
    s = session_factory()
    s.add(Yard(id=YARD_ID, name="审批测试小院"))
    s.add(
        User(
            id=ADMIN_ID,
            username="approval_admin",
            password_hash=hash_password("x"),
            display_name="小院管理员",
        )
    )
    s.add(
        User(
            id=EXPERT_ID,
            username="approval_expert",
            password_hash=hash_password("x"),
            display_name="专家",
        )
    )
    s.add(YardMembership(yard_id=YARD_ID, user_id=ADMIN_ID, role="yard_admin"))
    s.add(YardMembership(yard_id=YARD_ID, user_id=EXPERT_ID, role="expert"))
    s.commit()
    s.close()


@pytest.fixture()
def api_client(session_factory, seed_data):
    register_workorder_tool()
    app = create_app()

    def override_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_db
    app.include_router(tasks_router, prefix="/api/v1")
    app.include_router(tools_router, prefix="/api/v1")
    app.include_router(approvals_router, prefix="/api/v1")
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture(autouse=True)
def patch_worker_session(session_factory, monkeypatch):
    worker_tasks._session_factory = session_factory
    monkeypatch.setattr(settings, "worker_sync_fallback", False)
    yield
    worker_tasks._session_factory = None


def _admin_headers():
    return {"Authorization": f"Bearer {_token(ADMIN_ID, ['yard_admin'])}"}


def _expert_headers():
    return {"Authorization": f"Bearer {_token(EXPERT_ID, ['expert'])}"}


def _create_workorder_task(api_client, headers, title="测试工单"):
    resp = api_client.post(
        "/api/v1/tasks",
        json={"task_type": TASK_TYPE, "input": {"title": title}},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def test_risk_blocked_creates_approval_and_awaiting_review(api_client, session_factory):
    task_id = _create_workorder_task(api_client, _admin_headers())
    worker_tasks.execute_task(task_id)
    s = session_factory()
    task = s.get(Task, task_id)
    assert task.status == sm.STATUS_AWAITING_REVIEW, task.status
    assert task.result_json["stage"] == "awaiting_approval"
    approval_id = task.result_json["approval_id"]
    approval = s.get(ToolApproval, approval_id)
    assert approval is not None
    assert approval.status == "pending"
    assert approval.tool_code == "workorder.dispatch"
    assert approval.task_id == task_id
    s.close()


def test_approval_idempotent_for_same_params(api_client, session_factory):
    t1 = _create_workorder_task(api_client, _admin_headers(), title="同参工单")
    worker_tasks.execute_task(t1)
    t2 = _create_workorder_task(api_client, _admin_headers(), title="同参工单")
    worker_tasks.execute_task(t2)
    s = session_factory()
    pendings = (
        s.query(ToolApproval)
        .filter(
            ToolApproval.yard_id == YARD_ID,
            ToolApproval.tool_code == "workorder.dispatch",
            ToolApproval.status == "pending",
        )
        .all()
    )
    assert len(pendings) == 1
    s.close()


def test_approve_executes_tool_and_succeeds_task(api_client, session_factory):
    task_id = _create_workorder_task(api_client, _admin_headers(), title="待批准工单")
    worker_tasks.execute_task(task_id)
    s = session_factory()
    task = s.get(Task, task_id)
    approval_id = task.result_json["approval_id"]
    s.close()
    resp = api_client.post(
        f"/api/v1/tool-approvals/{approval_id}/approve",
        json={"note": "同意下发"},
        headers=_expert_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["approval"]["status"] == "approved"
    assert body["tool_result"]["dry_run"] is True
    s = session_factory()
    task = s.get(Task, task_id)
    assert task.status == sm.STATUS_SUCCEEDED
    assert task.result_json["approval_id"] == approval_id
    assert task.result_json["tool_result"]["dry_run"] is True
    approval = s.get(ToolApproval, approval_id)
    assert approval.decided_by == EXPERT_ID
    s.close()


def test_reject_cancels_task(api_client, session_factory):
    task_id = _create_workorder_task(api_client, _admin_headers(), title="待驳回工单")
    worker_tasks.execute_task(task_id)
    s = session_factory()
    approval_id = s.get(Task, task_id).result_json["approval_id"]
    s.close()
    resp = api_client.post(
        f"/api/v1/tool-approvals/{approval_id}/reject",
        json={"note": "暂不需要"},
        headers=_expert_headers(),
    )
    assert resp.status_code == 200, resp.text
    s = session_factory()
    assert s.get(Task, task_id).status == sm.STATUS_CANCELLED
    assert s.get(ToolApproval, approval_id).status == "rejected"
    s.close()


def test_default_role_policy_allows_self_approve(api_client, session_factory):
    """默认策略（role）：空间管理员对本人发起的审批单有审批权（审计标 self_approved）。"""
    from app.modules.audit.models import AuditLog

    task_id = _create_workorder_task(api_client, _admin_headers(), title="自批工单")
    worker_tasks.execute_task(task_id)
    s = session_factory()
    approval_id = s.get(Task, task_id).result_json["approval_id"]
    s.close()
    resp = api_client.post(
        f"/api/v1/tool-approvals/{approval_id}/approve",
        json={},
        headers=_admin_headers(),
    )
    assert resp.status_code == 200, resp.text
    s = session_factory()
    assert s.get(ToolApproval, approval_id).status == "approved"
    audit = (
        s.query(AuditLog).filter_by(action="tool_approval_approved", resource_id=approval_id).one()
    )
    assert audit.metadata_json.get("self_approved") is True
    s.close()


def test_never_policy_blocks_self_decide(api_client, session_factory, monkeypatch):
    """never 策略（严格四眼合规）：管理员也不能自批自己发起的申请。"""
    from app.core.config import settings as _cfg

    monkeypatch.setattr(_cfg, "tool_approval_self_decide", "never")
    task_id = _create_workorder_task(api_client, _admin_headers(), title="禁用自批工单")
    worker_tasks.execute_task(task_id)
    s = session_factory()
    approval_id = s.get(Task, task_id).result_json["approval_id"]
    s.close()
    resp = api_client.post(
        f"/api/v1/tool-approvals/{approval_id}/approve",
        json={},
        headers=_admin_headers(),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "APPROVAL_SELF_DECIDE"


def test_operator_cannot_approve_at_all(api_client, session_factory):
    """普通成员（operator）无审批权：只能发起，审批入口一律 403。"""
    from app.core.security import hash_password
    from app.modules.auth.models import User
    from app.modules.yards.models import YardMembership

    op_id = "user-approval-op"
    s = session_factory()
    s.add(
        User(
            id=op_id,
            username="approval_op",
            password_hash=hash_password("x"),
            display_name="运营员",
        )
    )
    s.add(YardMembership(yard_id=YARD_ID, user_id=op_id, role="operator"))
    s.commit()
    # 用 admin 发起，operator 尝试审批
    s.close()
    task_id = _create_workorder_task(api_client, _admin_headers(), title="运营员试批")
    worker_tasks.execute_task(task_id)
    s = session_factory()
    approval_id = s.get(Task, task_id).result_json["approval_id"]
    s.close()
    resp = api_client.post(
        f"/api/v1/tool-approvals/{approval_id}/approve",
        json={},
        headers={"Authorization": f"Bearer {_token(op_id, ['operator'])}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_list_approvals(api_client, session_factory):
    task_id = _create_workorder_task(api_client, _admin_headers(), title="列表工单")
    worker_tasks.execute_task(task_id)
    resp = api_client.get("/api/v1/tool-approvals?status=pending", headers=_admin_headers())
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]
    assert any(i["task_id"] == task_id for i in items)


def test_cross_yard_approval_invisible(api_client, session_factory):
    task_id = _create_workorder_task(api_client, _admin_headers(), title="隔离工单")
    worker_tasks.execute_task(task_id)
    s = session_factory()
    approval_id = s.get(Task, task_id).result_json["approval_id"]
    s.close()
    cross_token = create_access_token(
        user_id="user-other", yard_id="yard-other", roles=["yard_admin"]
    )
    cross = {"Authorization": f"Bearer {cross_token}"}
    resp = api_client.post(f"/api/v1/tool-approvals/{approval_id}/approve", json={}, headers=cross)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "APPROVAL_NOT_FOUND"


# ---------------------------------------------------------------------------
# 自批策略（四眼原则的单人空间豁免）
# ---------------------------------------------------------------------------


def test_solo_admin_can_self_approve(api_client, session_factory, monkeypatch):
    """solo 策略：本空间除发起人外无可审批人时，允许自批并标 self_approved。"""
    from app.modules.audit.models import AuditLog
    from app.modules.yards.models import YardMembership

    monkeypatch.setattr(settings, "tool_approval_self_decide", "solo")
    # 撤销 expert 成员身份 → admin 成为唯一可审批人
    s = session_factory()
    exp_mem = s.query(YardMembership).filter_by(yard_id=YARD_ID, user_id=EXPERT_ID).one()
    s.delete(exp_mem)
    s.commit()
    s.close()

    task_id = _create_workorder_task(api_client, _admin_headers(), title="单人自批工单")
    worker_tasks.execute_task(task_id)
    s = session_factory()
    approval_id = s.get(Task, task_id).result_json["approval_id"]
    s.close()

    resp = api_client.post(
        f"/api/v1/tool-approvals/{approval_id}/approve",
        json={},
        headers=_admin_headers(),
    )
    assert resp.status_code == 200, resp.text
    s = session_factory()
    assert s.get(ToolApproval, approval_id).status == "approved"
    assert s.get(Task, task_id).status == sm.STATUS_SUCCEEDED
    # 审计必须标记 self_approved=True（可追责）
    audit = (
        s.query(AuditLog).filter_by(action="tool_approval_approved", resource_id=approval_id).one()
    )
    assert audit.metadata_json.get("self_approved") is True
    s.close()


def test_solo_policy_multi_approver_blocks_self_decide(api_client, session_factory, monkeypatch):
    """solo 策略 + 空间另有 active 专家 → 管理员自批被拒（四眼仍然成立）。"""
    monkeypatch.setattr(settings, "tool_approval_self_decide", "solo")
    task_id = _create_workorder_task(api_client, _admin_headers(), title="双审批人禁自批")
    worker_tasks.execute_task(task_id)
    s = session_factory()
    approval_id = s.get(Task, task_id).result_json["approval_id"]
    s.close()
    resp = api_client.post(
        f"/api/v1/tool-approvals/{approval_id}/approve",
        json={},
        headers=_admin_headers(),
    )
    assert resp.status_code == 403
    # 但专家可以批（权限跟人走，与是否本人发起无关）
    resp2 = api_client.post(
        f"/api/v1/tool-approvals/{approval_id}/approve",
        json={},
        headers=_expert_headers(),
    )
    assert resp2.status_code == 200, resp2.text


def test_expert_disabled_not_counted_in_solo(api_client, session_factory, monkeypatch):
    """solo 只数 active 成员：专家被停用后管理员自批放行。"""
    from app.modules.yards.models import YardMembership

    monkeypatch.setattr(settings, "tool_approval_self_decide", "solo")
    s = session_factory()
    exp_mem = s.query(YardMembership).filter_by(yard_id=YARD_ID, user_id=EXPERT_ID).one()
    exp_mem.status = "disabled"
    s.commit()
    s.close()

    task_id = _create_workorder_task(api_client, _admin_headers(), title="停用专家后自批")
    worker_tasks.execute_task(task_id)
    s = session_factory()
    approval_id = s.get(Task, task_id).result_json["approval_id"]
    s.close()

    resp = api_client.post(
        f"/api/v1/tool-approvals/{approval_id}/approve",
        json={},
        headers=_admin_headers(),
    )
    assert resp.status_code == 200, resp.text
