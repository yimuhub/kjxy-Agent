"""统一错误信封：框架自带的 404/405 也必须套同一套结构。

背景：前端只认 {"error": {...}}。FastAPI 默认的 {"detail": ...} 会被前端
误判成「网络连接失败」——用户看到批量操作报「网络连接失败」，实际只是
后端进程还是旧版本、路由根本不存在。这里锁住信封结构与错误码。
"""


def test_未知路由返回统一错误信封(client):
    resp = client.get("/api/v1/definitely-not-a-real-route")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert "request_id" in body


def test_方法不允许返回统一错误信封(client):
    # 分支只开了 POST，用 GET 打过去应当是 405（不是 404，更不是「网络连接失败」）
    resp = client.get("/api/v1/chat/sessions/some-id/fork")
    assert resp.status_code == 405
    body = resp.json()
    assert body["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert body["request_id"]


def test_未登录访问受保护端点返回统一错误信封(client):
    resp = client.get("/api/v1/chat/sessions")
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "UNAUTHORIZED"
