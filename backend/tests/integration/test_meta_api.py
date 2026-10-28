"""版本握手接口（v3.21）：前端据此识别后端进程是否落后。"""

from __future__ import annotations


def test_meta_returns_version_and_migration_head(client):
    resp = client.get("/api/v1/meta")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    data = body["data"]
    assert data["version"] == client.app.version
    assert "migration_head" in data
    assert data["migration_head"] is None or isinstance(data["migration_head"], str)
    assert body["request_id"]


def test_meta_is_public(client):
    # 不带任何令牌也应可读：登录页也要能做版本握手
    resp = client.get("/api/v1/meta")
    assert resp.status_code == 200
