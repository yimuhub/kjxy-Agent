"""官方库 HTTP：权限边界、响应形状与离线状态。"""

from __future__ import annotations

from app.core.security import create_access_token
from app.modules.library import crypto

BASE = "/api/v1/library"
YARD = "yard-library-api"
SEED = bytes(range(32))
PUBLIC = crypto.public_key_b64_from_seed(SEED)


def _hdr(roles=("yard_admin",), yard_id: str = YARD, user_id: str = "u-library-api"):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


def test_source_permissions_and_status(client):
    resp = client.get(f"{BASE}/sources", headers=_hdr())
    assert resp.status_code == 200
    assert resp.json()["data"]["items"] == []

    official = {
        "name": "越权官方源",
        "kind": "official",
        "base_url": "https://library.test/stable/",
        "public_key_b64": PUBLIC,
    }
    forbidden = client.post(f"{BASE}/sources", json=official, headers=_hdr())
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "LIBRARY_FORBIDDEN"

    private = {
        "name": "本小院镜像",
        "kind": "private",
        "base_url": "https://mirror.test/library/",
        "public_key_b64": PUBLIC,
    }
    created = client.post(f"{BASE}/sources", json=private, headers=_hdr())
    assert created.status_code == 200, created.text
    private_id = created.json()["data"]["id"]
    assert created.json()["data"]["yard_id"] == YARD
    assert "public_key_b64" not in created.json()["data"]
    assert created.json()["data"]["key_id"]

    key_id = created.json()["data"]["key_id"]
    patched = client.patch(
        f"{BASE}/sources/{private_id}",
        # 前端编辑时 public_key_b64 留空 = 不更换公钥
        json={"name": "本小院镜像（改）", "public_key_b64": ""},
        headers=_hdr(),
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["name"] == "本小院镜像（改）"
    assert patched.json()["data"]["key_id"] == key_id

    # 停用后手动同步直接拒绝（不触网），验证空 body 的 POST 路由可用
    disabled = client.patch(
        f"{BASE}/sources/{private_id}", json={"enabled": False}, headers=_hdr()
    )
    assert disabled.status_code == 200
    sync = client.post(f"{BASE}/sources/{private_id}/sync", headers=_hdr())
    assert sync.status_code == 409
    assert sync.json()["error"]["code"] == "LIBRARY_SOURCE_DISABLED"

    global_source = client.post(
        f"{BASE}/sources",
        json=official,
        headers=_hdr(roles=("platform_admin",), yard_id="yard-platform"),
    )
    assert global_source.status_code == 200, global_source.text
    assert global_source.json()["data"]["yard_id"] is None

    # 官方源对普通小院可见但不可编辑
    listed = client.get(f"{BASE}/sources", headers=_hdr())
    assert listed.status_code == 200
    assert len(listed.json()["data"]["items"]) == 2
    denied = client.patch(
        f"{BASE}/sources/{global_source.json()['data']['id']}",
        json={"name": "改名"},
        headers=_hdr(),
    )
    assert denied.status_code == 403

    removed = client.delete(f"{BASE}/sources/{private_id}", headers=_hdr())
    assert removed.status_code == 200
    assert removed.json()["data"]["deleted"] is True


def test_status_and_empty_lists_are_offline(client):
    status_resp = client.get(f"{BASE}/status", headers=_hdr())
    assert status_resp.status_code == 200
    data = status_resp.json()["data"]
    assert data["offline"] is True
    assert data["installed"] == 0
    assert data["updatable"] == 0

    items = client.get(f"{BASE}/items", headers=_hdr())
    assert items.status_code == 200
    assert items.json()["data"]["items"] == []
    assert items.json()["data"]["offline"] is True

    installs = client.get(f"{BASE}/installs", headers=_hdr())
    assert installs.status_code == 200
    assert installs.json()["data"]["total"] == 0

    missing = client.post(f"{BASE}/items/not-exist/install", json={}, headers=_hdr())
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "LIBRARY_ITEM_NOT_FOUND"

    missing_install = client.post(f"{BASE}/installs/not-exist/rollback", headers=_hdr())
    assert missing_install.status_code == 404
    assert missing_install.json()["error"]["code"] == "LIBRARY_INSTALL_NOT_FOUND"
