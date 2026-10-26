"""市场 HTTP 接口：检索、详情、安装、导入与 GitHub 离线降级。"""

from __future__ import annotations

import io
import json
import zipfile

from app.core.errors import AppError
from app.core.security import create_access_token
from app.modules.market import router as market_router

BASE = "/api/v1/market"
YARD = "yard-market-api"


def _hdr(roles=("yard_admin",), user_id="u-market-api", yard_id: str = YARD) -> dict[str, str]:
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


def _zip_manifest() -> bytes:
    manifest = {
        "plugin_id": "api-pack",
        "name": "接口插件",
        "version": "1.0.0",
        "type": "knowledge_pack",
        "api_compatibility": "1.0",
        "dependencies": [],
        "permissions": [],
        "entry_point": None,
        "config_schema": {},
        "checksum": "",
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("README.md", "# api")
    return buffer.getvalue()


def test_search_detail_and_install_flow(client):
    resp = client.post(f"{BASE}/items/search", json={"query": "轮作"}, headers=_hdr())
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert [i["code"] for i in data["items"]] == ["agri_soil_health"]
    assert data["warning"] is None

    key = data["items"][0]["item_key"]
    detail = client.get(f"{BASE}/items/{key}", headers=_hdr())
    assert detail.status_code == 200
    body = detail.json()["data"]
    assert body["definition_json"]["prompt"]
    assert body["installed"] is False

    installed = client.post(f"{BASE}/items/{key}/install", json={}, headers=_hdr())
    assert installed.status_code == 200, installed.text
    assert installed.json()["data"]["skill_status"] == "published"

    listed = client.get(f"{BASE}/installed", headers=_hdr())
    assert listed.status_code == 200
    assert [s["code"] for s in listed.json()["data"]["skills"]] == ["agri_soil_health"]


def test_skill_import_endpoint_creates_pending(client):
    text = (
        "---\nname: 接口导入技能\ndescription: 测试导入\n---\n# 接口导入技能\n\n## Purpose\n描述。"
    )
    resp = client.post(
        f"{BASE}/skills/import",
        files={"file": ("api-skill.md", text.encode("utf-8"), "text/markdown")},
        headers=_hdr(),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["code"] == "api-skill"
    assert data["skill_status"] == "pending_review"

    install = client.post(f"{BASE}/items/{data['item_key']}/install", json={}, headers=_hdr())
    assert install.status_code == 200
    assert install.json()["data"]["review_status"] == "pending_review"


def test_plugin_import_review_gate_blocks_install_only_role(client, db_session):
    """仅有 market:install 的角色（DB 覆盖）仍不能导入插件。"""
    from app.modules.yards.models import YardMembership, YardRole
    from app.modules.yards.repository import create_yard

    yard = create_yard(db_session, name="导入审核小院")
    db_session.add(
        YardRole(
            yard_id=yard.id,
            code="expert",
            name="专家",
            permissions_json=["market:install", "knowledge:read"],
            is_system=True,
        )
    )
    db_session.add(
        YardMembership(yard_id=yard.id, user_id="u-review", role="expert", status="active")
    )
    db_session.commit()

    files = {"file": ("plugin.zip", _zip_manifest(), "application/zip")}
    resp = client.post(
        f"{BASE}/plugins/import",
        files=files,
        headers=_hdr(roles=("expert",), user_id="u-review", yard_id=yard.id),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_plugin_import_requires_review_permission(client):
    files = {"file": ("plugin.zip", _zip_manifest(), "application/zip")}
    denied = client.post(f"{BASE}/plugins/import", files=files, headers=_hdr(roles=("viewer",)))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "FORBIDDEN"

    files = {"file": ("plugin.zip", _zip_manifest(), "application/zip")}
    allowed = client.post(f"{BASE}/plugins/import", files=files, headers=_hdr())
    assert allowed.status_code == 200, allowed.text
    data = allowed.json()["data"]
    assert data["plugin_status"] == "pending_review"

    # expert 有市场安装权，但插件安装必须管理员/负责人
    expert_install = client.post(
        f"{BASE}/items/{data['item_key']}/install", json={}, headers=_hdr(roles=("expert",))
    )
    assert expert_install.status_code == 403
    assert expert_install.json()["error"]["code"] == "MARKET_FORBIDDEN"

    admin_install = client.post(f"{BASE}/items/{data['item_key']}/install", json={}, headers=_hdr())
    assert admin_install.status_code == 200, admin_install.text
    assert admin_install.json()["data"]["plugin_status"] == "pending_review"


def test_github_search_offline_is_not_500(client, monkeypatch):
    def boom(query, limit=10):
        raise AppError(
            "NETWORK_UNAVAILABLE",
            "暂时无法连接 GitHub。",
            http_status=409,
            details={"mode": "local"},
        )

    monkeypatch.setattr(market_router, "search_github_skills", boom)
    resp = client.post(f"{BASE}/github/search", json={"query": "soil"}, headers=_hdr())
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["items"] == []
    assert data["offline"] is True
    assert "GitHub" in data["warning"]


def test_github_search_caches_installable_item(client, monkeypatch):
    from app.modules.market import installer

    monkeypatch.setattr(
        market_router,
        "search_github_skills",
        lambda query, limit=10: [
            {
                "repo": "owner/mit-farm",
                "description": "farm skill",
                "stars": 7,
                "license": "MIT",
                "updated_at": "2026-01-01T00:00:00Z",
                "default_branch": "main",
                "html_url": "https://github.com/owner/mit-farm",
            }
        ],
    )
    monkeypatch.setattr(
        installer,
        "preview_skill_from_repo",
        lambda repo, ref=None: {
            "name": "MIT Farm",
            "version": "1.0.0",
            "description": "farm skill",
            "source_url": "https://github.com/owner/mit-farm/blob/main/SKILL.md",
            "license": "MIT",
            "definition_json": {
                "name": "MIT Farm",
                "description": "farm skill",
                "prompt": "按步骤执行。",
                "steps": ["收集信息"],
            },
        },
    )
    resp = client.post(f"{BASE}/github/search", json={"query": "farm"}, headers=_hdr())
    assert resp.status_code == 200, resp.text
    item = resp.json()["data"]["items"][0]
    assert item["item_key"] == "github:skill:owner-mit-farm"

    detail = client.get(f"{BASE}/items/{item['item_key']}", headers=_hdr())
    assert detail.status_code == 200

    install = client.post(f"{BASE}/items/{item['item_key']}/install", json={}, headers=_hdr())
    assert install.status_code == 200, install.text
    assert install.json()["data"]["skill_status"] == "pending_review"


def test_github_preview_endpoint(client, monkeypatch):
    def fake_preview(repo, ref=None):
        return {
            "repo": repo,
            "ref": ref or "main",
            "name": "Demo",
            "license": "MIT",
            "definition_json": {"name": "Demo", "prompt": "步骤。", "steps": ["一"]},
        }

    monkeypatch.setattr(market_router, "preview_skill_from_repo", fake_preview)
    resp = client.post(
        f"{BASE}/github/preview", json={"repo": "owner/demo", "ref": "main"}, headers=_hdr()
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["definition_json"]["prompt"] == "步骤。"


def test_market_endpoints_require_permission(client):
    resp = client.post(f"{BASE}/items/search", json={}, headers=_hdr(roles=("viewer",)))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"
