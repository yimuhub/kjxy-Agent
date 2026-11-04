"""工作日志 API 集成测试（v3.58）：operator 全流程 + 权限边界 + 监管聚合。"""

from types import SimpleNamespace

import pytest
from app.core.deps import get_db_session
from app.core.security import create_access_token
from app.db.base import Base
from app.main import create_app
from app.modules.audit.models import AuditLog
from app.modules.yards.models import YardMembership
from app.modules.yards.repository import create_yard
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DAY = None


def _hdr(user_id="u-op", yard_id=None, roles=("operator",)):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'journal-test.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    yield factory
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def api_client(session_factory):
    app = create_app()

    def override_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_db
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture()
def yard(session_factory):
    db = session_factory()
    try:
        y = create_yard(db, name="日志集成小院")
        db.add(YardMembership(yard_id=y.id, user_id="u-op", role="operator"))
        db.add(YardMembership(yard_id=y.id, user_id="u-expert", role="expert"))
        db.add(
            AuditLog(
                yard_id=y.id,
                actor_id="u-op",
                action="farm.record",
                resource_type="test",
                resource_id="x",
                result="success",
                request_id="req",
            )
        )
        db.commit()
        return SimpleNamespace(id=y.id)
    finally:
        db.close()


def _api(api_client, yard):
    class Api:
        def get(self, path, **kw):
            return api_client.get(
                f"/api/v1{path}", headers=kw.get("headers"), params=kw.get("params")
            )

        def post(self, path, json=None, **kw):
            return api_client.post(f"/api/v1{path}", json=json, headers=kw.get("headers"))

        def put(self, path, json=None, **kw):
            return api_client.put(f"/api/v1{path}", json=json, headers=kw.get("headers"))

    return Api()


def test_full_flow_operator_to_expert_review(api_client, session_factory, yard):
    api = _api(api_client, yard)
    op = _hdr(yard_id=yard.id)
    # ① 生成草稿
    r = api.post("/journal/draft", {"kind": "daily"}, headers=op)
    assert r.status_code == 200, r.text
    row = r.json()["data"]
    assert row["status"] == "draft" and "农事台账记录" in row["content_md"]
    # 幂等
    r2 = api.post("/journal/draft", {"kind": "daily"}, headers=op)
    assert r2.json()["data"]["id"] == row["id"]
    # ② 编辑 + 提交
    r = api.put(
        f"/journal/{row['id']}", {"content_md": "# 工作日志\n- 巡塘两次\n- 培训 20 人"}, headers=op
    )
    assert r.status_code == 200
    r = api.post(f"/journal/{row['id']}/submit", {}, headers=op)
    assert r.json()["data"]["status"] == "submitted"
    # ③ operator 无审核权 → 403
    r = api.post(f"/journal/{row['id']}/review", {"action": "approve"}, headers=op)
    assert r.status_code == 403
    # ④ expert 审核通过
    expert = _hdr(user_id="u-expert", yard_id=yard.id, roles=("expert",))
    r = api.post(
        f"/journal/{row['id']}/review", {"action": "approve", "note": "合格"}, headers=expert
    )
    assert r.json()["data"]["status"] == "approved"
    # ⑤ 统计
    r = api.get("/journal/stats", headers=op)
    assert r.json()["data"]["daily_approved"] == 1
    # ⑥ 全院视图：operator 403，expert 可见
    r = api.get("/journal", params={"scope": "yard"}, headers=op)
    assert r.status_code == 403
    r = api.get("/journal", params={"scope": "yard"}, headers=expert)
    assert r.status_code == 200 and len(r.json()["data"]["items"]) == 1


def test_monthly_export_docx(api_client, yard):
    api = _api(api_client, yard)
    op = _hdr(yard_id=yard.id)
    api.post("/journal/draft", {"kind": "daily"}, headers=op)
    r = api.get("/journal/monthly/export", headers=op)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert len(r.content) > 1000


def test_aggregate_requires_platform_admin(api_client, session_factory, yard):
    api = _api(api_client, yard)
    month = __import__("datetime").datetime.now(__import__("datetime").UTC).strftime("%Y-%m")
    r = api.get("/journal/aggregate", params={"month": month}, headers=_hdr(yard_id=yard.id))
    assert r.status_code == 403
    admin = _hdr(user_id="u-admin", yard_id=yard.id, roles=("platform_admin",))
    r = api.get("/journal/aggregate", params={"month": month}, headers=admin)
    assert r.status_code == 200
    items = r.json()["data"]["items"]
    assert any(i["yard_id"] == yard.id for i in items)


def test_training_notify_pushes(api_client, session_factory, yard, monkeypatch):
    sent: list[tuple] = []
    import app.modules.push.service as push_service

    monkeypatch.setattr(
        push_service,
        "dispatch",
        lambda db, **kw: sent.append((kw.get("event_type"), kw.get("payload"))),
    )
    api = _api(api_client, yard)
    # operator 无权发起培训通知
    r = api.post(
        "/journal/training/notify",
        {"title": "河蟹塘口管理培训", "time_text": "周六 14:00", "place": "小院"},
        headers=_hdr(yard_id=yard.id),
    )
    assert r.status_code == 403
    # 负责人可发
    owner = _hdr(user_id="u-owner", yard_id=yard.id, roles=("yard_owner",))
    r = api.post(
        "/journal/training/notify",
        {"title": "河蟹塘口管理培训", "time_text": "周六 14:00", "place": "小院"},
        headers=owner,
    )
    assert r.status_code == 200
    assert sent and sent[0][0] == "training_reminder" and "培训" in sent[0][1]["title"]


def test_attachable_yards_endpoint(api_client, session_factory, yard):
    """绑定下拉的数据源：platform_admin 全部；普通 operator 只看到自己管理的院。"""
    from app.modules.yards.models import YardMembership

    db = session_factory()
    try:
        db.add(YardMembership(yard_id=yard.id, user_id="u-owner2", role="yard_owner"))
        db.commit()
    finally:
        db.close()

    admin = _hdr(user_id="u-admin2", yard_id=yard.id, roles=("platform_admin",))
    r = api_client.get("/api/v1/hubs/attachable-yards", headers=admin)
    assert r.status_code == 200, r.text
    ids = [i["id"] for i in r.json()["data"]]
    assert yard.id in ids

    operator = _hdr(user_id="u-op-plain", yard_id=yard.id, roles=("operator",))
    r2 = api_client.get("/api/v1/hubs/attachable-yards", headers=operator)
    assert r2.status_code == 200
    assert r2.json()["data"] == []  # operator 不管理任何小院
