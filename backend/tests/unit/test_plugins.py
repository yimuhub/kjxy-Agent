"""插件机制测试：扫描发现、manifest 校验、启用/禁用、404、领域包读取。

- 服务层用 db_session（SQLite 内存）；
- 路由层用 client + platform_admin JWT，走真实 /api/v1/plugins 端点。
"""

import json
import os

import pytest

os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-min-32-chars-for-unit-tests")
os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("EMBEDDING_PROVIDER", "mock")

from app.core.errors import AppError  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.modules.plugins import models as plugin_models  # noqa: E402,F401 触发建表
from app.modules.plugins import service  # noqa: E402


def _manifest(plugin_id: str, ptype: str = "workflow", **overrides) -> dict:
    m = {
        "plugin_id": plugin_id,
        "name": f"插件 {plugin_id}",
        "version": "1.0.0",
        "type": ptype,
        "api_compatibility": "1.0.0",
        "dependencies": [],
        "permissions": ["knowledge:read"],
        "entry_point": None,
        "config_schema": {},
        "description": f"测试插件 {plugin_id}",
    }
    m.update(overrides)
    if "checksum" not in overrides:
        m["checksum"] = service.compute_checksum(m)
    return m


def _write_plugin(tmp_path, rel_dir: str, manifest: dict) -> None:
    d = tmp_path / rel_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@pytest.fixture()
def plugin_root(tmp_path, monkeypatch):
    """隔离的插件目录：2 个合法 + 1 个缺字段 + 1 个 checksum 不符。"""
    _write_plugin(tmp_path, "crops/rice", _manifest("crops.rice", "knowledge_pack"))
    _write_plugin(tmp_path, "demo/hello", _manifest("demo.hello", "workflow"))
    bad = _manifest("bad.missing")
    del bad["version"]
    bad["checksum"] = service.compute_checksum(bad)
    _write_plugin(tmp_path, "bad/missing", bad)
    tampered = _manifest("bad.checksum")
    tampered["checksum"] = "0" * 64
    _write_plugin(tmp_path, "bad/checksum", tampered)
    monkeypatch.setenv("PLUGIN_DIR", str(tmp_path))
    return tmp_path


def test_scan_discovers_and_registers(db_session, plugin_root):
    report = service.scan_plugins(db_session)
    db_session.commit()

    assert report["discovered"] == 3  # 2 合法 + 1 checksum 不符照常注册
    assert report["registered"] == 3
    assert len(report["errors"]) == 1  # 缺字段的 manifest
    assert "version" in report["errors"][0]["error"]
    assert len(report["checksum_warnings"]) == 1
    assert report["checksum_warnings"][0]["plugin_id"] == "bad.checksum"

    records = {r.plugin_id: r for r in service.list_plugins(db_session)}
    assert {"crops.rice", "demo.hello", "bad.checksum"} <= set(records)
    # 新插件默认 disabled，全局 scope（yard_id 为空）
    assert records["demo.hello"].status == "disabled"
    assert records["demo.hello"].yard_id is None
    assert records["demo.hello"].checksum_ok is True
    assert records["bad.checksum"].checksum_ok is False


def test_scan_real_repo_dir_finds_rice_and_hello(db_session, monkeypatch):
    """真实 plugins/ 目录：能发现水稻知识包与 hello 演示插件。"""
    monkeypatch.delenv("PLUGIN_DIR", raising=False)
    report = service.scan_plugins(db_session)
    db_session.commit()
    found = {r.plugin_id for r in service.list_plugins(db_session)}
    assert "crops.rice" in found
    assert "demo.hello" in found
    # 水稻包的 checksum 是占位符，应告警但不阻断
    assert any(w["plugin_id"] == "crops.rice" for w in report["checksum_warnings"])
    assert report["discovered"] >= 2


def test_manifest_missing_field_raises(db_session):
    bad = _manifest("x.y")
    del bad["name"]
    with pytest.raises(AppError) as exc:
        service.validate_manifest(bad, source="x/manifest.json")
    assert exc.value.code == "PLUGIN_MANIFEST_INVALID"
    assert exc.value.http_status == 422


def test_manifest_unsupported_type_raises():
    bad = _manifest("x.y", ptype="model_adapter")
    with pytest.raises(AppError) as exc:
        service.validate_manifest(bad)
    assert exc.value.code == "PLUGIN_TYPE_UNSUPPORTED"


def test_enable_disable_flip(db_session, plugin_root):
    service.scan_plugins(db_session)
    db_session.commit()

    rec = service.enable_plugin(db_session, "demo.hello")
    assert rec.status == "enabled"
    rec = service.disable_plugin(db_session, "demo.hello")
    assert rec.status == "disabled"
    db_session.commit()

    # 重扫保留状态：先启用再重扫，仍为 enabled
    service.enable_plugin(db_session, "demo.hello")
    service.scan_plugins(db_session)
    assert service.get_plugin(db_session, "demo.hello").status == "enabled"


def test_unknown_plugin_id_404(db_session, plugin_root):
    service.scan_plugins(db_session)
    with pytest.raises(AppError) as exc:
        service.enable_plugin(db_session, "nope.unknown")
    assert exc.value.code == "PLUGIN_NOT_FOUND"
    assert exc.value.http_status == 404


def test_get_active_domain_packs(db_session, plugin_root, tmp_path):
    _write_plugin(tmp_path, "domain/crop_rules", _manifest("domain.crop_rules", "domain_pack"))
    service.scan_plugins(db_session)
    db_session.commit()

    assert service.get_active_domain_packs(db_session) == []  # 默认 disabled
    service.enable_plugin(db_session, "domain.crop_rules")
    packs = service.get_active_domain_packs(db_session)
    assert len(packs) == 1
    assert packs[0]["plugin_id"] == "domain.crop_rules"


def _admin_token() -> str:
    return create_access_token(user_id="u-admin", yard_id=None, roles=["platform_admin"])


def test_router_endpoints(client, plugin_root):
    headers = {"Authorization": f"Bearer {_admin_token()}"}

    r = client.post("/api/v1/plugins/scan", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["discovered"] == 3

    r = client.get("/api/v1/plugins", headers=headers)
    assert r.status_code == 200
    items = {i["plugin_id"]: i for i in r.json()["data"]}
    assert items["demo.hello"]["status"] == "disabled"
    assert items["demo.hello"]["type"] == "workflow"
    assert items["demo.hello"]["version"] == "1.0.0"

    r = client.post("/api/v1/plugins/demo.hello/enable", headers=headers)
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "enabled"

    r = client.post("/api/v1/plugins/demo.hello/disable", headers=headers)
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "disabled"
    # 停用只改状态，记录仍在
    r = client.get("/api/v1/plugins", headers=headers)
    assert "demo.hello" in {i["plugin_id"] for i in r.json()["data"]}

    # 未知 plugin_id -> 404
    r = client.post("/api/v1/plugins/nope.unknown/enable", headers=headers)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "PLUGIN_NOT_FOUND"

    # 未登录 -> 401
    r = client.get("/api/v1/plugins")
    assert r.status_code == 401


def test_router_mutation_requires_admin(client, plugin_root):
    user_token = create_access_token(user_id="u-1", yard_id="t-1", roles=["operator"])
    headers = {"Authorization": f"Bearer {user_token}"}
    r = client.post("/api/v1/plugins/demo.hello/enable", headers=headers)
    assert r.status_code == 403
