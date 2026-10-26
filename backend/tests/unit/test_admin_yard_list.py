"""GET /admin/yards 平台管理员全局小院列表：权限 + 聚合数字边界（ADR-0002 收敛）。

用 conftest 的 client/db_session fixture（内存 SQLite + 全量路由），
不依赖 backend/data/dev.db 的演示账号。
"""

import os

import pytest
from app.modules.auth import repository as auth_repo
from app.modules.yards import repository as yard_repo
from sqlalchemy.orm import Session

# 保证 platform_admin bootstrap 名单确定（Settings 默认即 "admin"，显式固定）
os.environ["PLATFORM_ADMIN_USERNAMES"] = "admin"


def _login(client, username: str, password: str):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["data"]["access_token"]


@pytest.fixture()
def seeded(db_session: Session):
    """一个小院 + 平台管理员(admin) + 小院管理员(ta) + 普通成员(bob)。"""
    db = db_session
    t = yard_repo.create_yard(db, name="测试小院")
    admin = auth_repo.create_user(db, username="admin", password="pw-admin")
    ta = auth_repo.create_user(db, username="ta", password="pw-ta")
    bob = auth_repo.create_user(db, username="bob", password="pw-bob")
    yard_repo.add_membership(db, yard_id=t.id, user_id=ta.id, role="yard_admin")
    yard_repo.add_membership(db, yard_id=t.id, user_id=bob.id, role="viewer")
    db.commit()
    return {"yard": t, "admin": admin, "ta": ta, "bob": bob}


def test_admin_yard_list_platform_admin(client, db_session: Session, seeded) -> None:
    tok = _login(client, "admin", "pw-admin")
    r = client.get("/api/v1/admin/yards", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["count"] == 1
    t0 = body["yards"][0]
    # 聚合数字边界：只有成员/任务计数，不得出现业务正文或用户 PII
    assert t0["name"] == "测试小院"
    assert t0["active_members"] == 2
    assert t0["task_total"] == 0
    assert set(t0) <= {
        "id",
        "name",
        "status",
        "province",
        "scene_type",
        "active_members",
        "task_total",
        "created_at",
    }


def test_admin_yard_list_yard_admin_forbidden(client, seeded) -> None:
    tok = _login(client, "ta", "pw-ta")
    r = client.get("/api/v1/admin/yards", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403


def test_admin_yard_list_unauthenticated(client, seeded) -> None:
    r = client.get("/api/v1/admin/yards")
    assert r.status_code in (401, 403)


def test_admin_stats_shape_matches_contract(client, seeded) -> None:
    """/admin/stats 字段形状（Dashboard/GlobalOverview 依赖）。"""
    tok = _login(client, "admin", "pw-admin")
    r = client.get("/api/v1/admin/stats", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["scope"] == "global"
    assert "total" in data["tasks"] and "by_status" in data["tasks"]
    assert "total_calls" in data["llm"] and "by_provider" in data["llm"]
    assert data["members"]["total"] == 2
