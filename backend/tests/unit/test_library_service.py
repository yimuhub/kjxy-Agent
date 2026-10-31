"""官方库服务：注入 fetcher 的同步/安装/更新/回滚/自动更新/离线兜底。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.audit.models import AuditLog
from app.modules.evolution.models import Skill
from app.modules.library import crypto, package, service
from app.modules.library.models import LibraryInstall, LibraryItem, LibrarySource
from app.modules.market.models import MarketItem
from app.modules.plugins.models import PluginManifest

SEED = bytes(range(32))
PUBLIC = crypto.public_key_b64_from_seed(SEED)
MANIFEST_URL = "https://library.test/stable/manifest.json"
YARD = "yard-library"


def _ctx(user_id: str = "u-library") -> YardContext:
    return YardContext(yard_id=YARD, user_id=user_id, roles=["yard_admin"], request_id="req_lib")


def _platform_ctx() -> YardContext:
    return YardContext(
        yard_id="yard-platform",
        user_id="u-platform",
        roles=["platform_admin"],
        request_id="req_platform",
    )


def _definition(code: str, version: str, risk: str = "low") -> dict:
    return {
        "code": code,
        "name": f"技能 {code}",
        "version": version,
        "category": "other",
        "summary": "官方演示技能",
        "when_to_use": "需要把零散事实整理成建议时使用。",
        "inputs": [],
        "steps": [{"kind": "prompt", "title": "整理", "text": "把事实整理成三条建议"}],
        "allowed_tools": [],
        "knowledge_scopes": ["yard"],
        "prompt": "请把用户给出的事实整理成三条可执行建议，并显式标注不确定的字段。",
        "examples": [],
        "eval_cases": [
            {"input": "整理今天的灌溉记录", "expect": "输出三条建议"},
            {"input": "没有数据", "expect": "列出需要补录的数据"},
        ],
        "guardrails": {
            "risk_level": risk,
            "max_steps": 5,
            "require_citation": False,
        },
        "output_format": "markdown",
        "sources": [],
        "tags": [],
        "license": "内部原创",
    }


def _build(code: str, version: str, risk: str = "low") -> bytes:
    return package.build_package(
        manifest={
            "kind": "skill",
            "code": code,
            "name": f"技能 {code}",
            "version": version,
            "channel": "stable",
            "risk_level": risk,
            "published_at": "2026-10-01T00:00:00+00:00",
        },
        definition=_definition(code, version, risk),
        seed=SEED,
    )


def _entry(code: str, version: str, data: bytes, risk: str = "low") -> dict:
    return {
        "item_type": "skill",
        "code": code,
        "name": f"技能 {code}",
        "version": version,
        "description": "官方演示技能",
        "category": "other",
        "risk_level": risk,
        "license": "内部原创",
        "author": "官方",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "download_url": f"https://library.test/packages/{code}-{version}.kjxpack",
        "published_at": "2026-10-01T00:00:00+00:00",
    }


def _plugin_definition(version: str) -> dict:
    return {
        "plugin_id": "demo_plugin",
        "name": "官方演示插件",
        "version": version,
        "type": "knowledge_pack",
        "api_compatibility": "1.0",
        "dependencies": [],
        "permissions": [],
        "entry_point": None,
        "config_schema": {},
        "checksum": "",
        "description": "官方声明式插件",
    }


def _build_plugin(version: str) -> bytes:
    return package.build_package(
        manifest={
            "kind": "plugin",
            "code": "demo_plugin",
            "name": "官方演示插件",
            "version": version,
            "channel": "stable",
        },
        definition=_plugin_definition(version),
        seed=SEED,
    )


def _plugin_entry(version: str, data: bytes) -> dict:
    entry = _entry("demo_plugin", version, data)
    entry.update(
        {
            "item_type": "plugin",
            "name": "官方演示插件",
            "description": "官方声明式插件",
            "download_url": f"https://library.test/packages/demo_plugin-{version}.kjxpack",
        }
    )
    return entry


def _manifest_body(sequence: int, items: list[dict], removed: list[str] | None = None) -> dict:
    now = datetime.now(UTC)
    body = {
        "schema_version": 1,
        "channel": "stable",
        "sequence": sequence,
        "generated_at": now.isoformat(),
        "expires_at": (now + timedelta(days=30)).isoformat(),
        "publisher": {"name": "官方", "key_id": crypto.public_key_fingerprint(PUBLIC)["key_id"]},
        "items": items,
    }
    if removed:
        body["removed"] = removed
    return crypto.sign_manifest(body, SEED)


class FakeFetcher:
    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, headers: dict):
        self.calls.append((url, dict(headers)))
        route = self.routes.get(url)
        if route is None:
            return {"status_code": 404, "content": b"", "headers": {}}
        return route(headers) if callable(route) else route


def _json_response(body: dict, headers: dict | None = None) -> dict:
    return {
        "status_code": 200,
        "content": json.dumps(body, ensure_ascii=False).encode("utf-8"),
        "headers": headers or {},
    }


def _binary_response(data: bytes) -> dict:
    return {"status_code": 200, "content": data, "headers": {}}


def _make_source(db_session, name: str = "官方源") -> str:
    created = service.create_source(
        db_session,
        _platform_ctx(),
        {
            "name": name,
            "kind": "official",
            "base_url": "https://library.test/stable/",
            "channel": "stable",
            "public_key_b64": PUBLIC,
        },
        is_platform_admin=True,
    )
    return created["id"]


def test_sync_adds_mirrors_and_skips_stale_sequence(db_session):
    source_id = _make_source(db_session)
    data = _build("demo_skill", "1.0.0")
    body = _manifest_body(1, [_entry("demo_skill", "1.0.0", data)])
    fetcher = FakeFetcher({MANIFEST_URL: _json_response(body, {"ETag": '"v1"'})})

    result = service.sync_source(db_session, YARD, source_id, fetcher)
    assert result["status"] == "ok"
    assert result["added"] == 1
    assert result["updated"] == 0
    db_session.flush()

    item = db_session.query(LibraryItem).one()
    assert item.item_key == "official:skill:demo_skill"
    market = db_session.query(MarketItem).one()
    assert market.source == "official"
    assert market.verified is True
    assert market.item_key == "official:skill:demo_skill"
    source = db_session.get(LibrarySource, source_id)
    assert source.sequence == 1
    assert source.etag == '"v1"'
    assert source.status == "ok"
    state = service.status(db_session, _ctx())
    assert state["offline"] is False
    assert state["last_sync_at"] is not None

    # 同一 sequence 的重放：跳过且不写条目
    replay = service.sync_source(db_session, YARD, source_id, fetcher)
    assert replay["status"] == "skipped"
    assert replay["reason"] == "stale_sequence"
    assert db_session.query(LibraryItem).count() == 1


def test_sync_etag_304_skips(db_session):
    source_id = _make_source(db_session)
    data = _build("demo_skill", "1.0.0")
    body = _manifest_body(1, [_entry("demo_skill", "1.0.0", data)])
    fetcher = FakeFetcher({MANIFEST_URL: _json_response(body, {"ETag": '"v1"'})})
    service.sync_source(db_session, YARD, source_id, fetcher)

    def not_modified(headers: dict) -> dict:
        assert headers.get("If-None-Match") == '"v1"'
        return {"status_code": 304, "content": b"", "headers": {}}

    fetcher.routes[MANIFEST_URL] = not_modified
    result = service.sync_source(db_session, YARD, source_id, fetcher)
    assert result["status"] == "skipped"
    assert result["reason"] == "not_modified"
    assert db_session.query(LibraryItem).count() == 1


def test_sync_updates_version_and_revokes(db_session):
    source_id = _make_source(db_session)
    v1 = _build("demo_skill", "1.0.0")
    v2 = _build("demo_skill", "1.1.0")
    fetcher = FakeFetcher(
        {
            MANIFEST_URL: _json_response(
                _manifest_body(1, [_entry("demo_skill", "1.0.0", v1)])
            )
        }
    )
    service.sync_source(db_session, YARD, source_id, fetcher)
    db_session.flush()

    fetcher.routes[MANIFEST_URL] = _json_response(
        _manifest_body(2, [_entry("demo_skill", "1.1.0", v2)])
    )
    result = service.sync_source(db_session, YARD, source_id, fetcher)
    assert result["added"] == 1
    assert db_session.query(LibraryItem).count() == 2
    market = db_session.query(MarketItem).one()
    assert market.version == "1.1.0"

    fetcher.routes[MANIFEST_URL] = _json_response(
        _manifest_body(3, [], removed=["official:skill:demo_skill"])
    )
    result = service.sync_source(db_session, YARD, source_id, fetcher)
    assert result["removed"] == 1
    statuses = {row.status for row in db_session.query(LibraryItem).all()}
    assert statuses == {"revoked"}
    assert db_session.query(MarketItem).one().status == "blocked"


def test_sync_rejects_bad_signature_and_does_not_raise_for_run_due(db_session):
    source_id = _make_source(db_session, name="坏签名源")
    body = _manifest_body(1, [])
    body["items"] = [{"item_type": "skill", "code": "x", "version": "1.0.0"}]
    body["signature"] = body["signature"][:-4] + "AAAA"
    fetcher = FakeFetcher({MANIFEST_URL: _json_response(body)})
    with pytest.raises(AppError) as exc:
        service.sync_source(db_session, YARD, source_id, fetcher)
    assert exc.value.code == "LIBRARY_SIGNATURE_INVALID"
    source = db_session.get(LibrarySource, source_id)
    assert source.status == "error"
    assert "签名" in source.last_error

    # 定时入口吞掉异常，只在结果里回报 error
    outcome = service.run_due_library_syncs(db_session, fetcher)
    assert outcome["processed"] == 1
    assert outcome["results"][0]["status"] == "error"


def test_install_is_idempotent_and_update_rollback(db_session):
    source_id = _make_source(db_session)
    v1 = _build("demo_skill", "1.0.0")
    v2 = _build("demo_skill", "1.1.0")
    routes = {
        MANIFEST_URL: _json_response(_manifest_body(1, [_entry("demo_skill", "1.0.0", v1)])),
        "https://library.test/packages/demo_skill-1.0.0.kjxpack": _binary_response(v1),
        "https://library.test/packages/demo_skill-1.1.0.kjxpack": _binary_response(v2),
    }
    fetcher = FakeFetcher(routes)
    service.sync_source(db_session, YARD, source_id, fetcher)
    item = db_session.query(LibraryItem).one()
    ctx = _ctx()

    installed = service.install_item(db_session, ctx, item.id, fetcher=fetcher)
    assert installed["idempotent"] is False
    assert installed["installed_version"] == "1.0.0"
    install = db_session.query(LibraryInstall).one()
    assert install.installed_version == "1.0.0"
    skill_v1 = db_session.query(Skill).filter(Skill.version == "1.0.0").one()
    assert skill_v1.status == "published"

    again = service.install_item(db_session, ctx, item.id, fetcher=fetcher)
    assert again["idempotent"] is True
    assert db_session.query(Skill).count() == 1

    routes[MANIFEST_URL] = _json_response(
        _manifest_body(2, [_entry("demo_skill", "1.1.0", v2)])
    )
    service.sync_source(db_session, YARD, source_id, fetcher)
    updated = service.update_item(db_session, ctx, install.id, fetcher=fetcher)
    assert updated["updated"] is True
    assert updated["installed_version"] == "1.1.0"
    assert updated["previous_version"] == "1.0.0"
    assert db_session.query(Skill).count() == 2
    assert (
        db_session.query(Skill).filter(Skill.version == "1.1.0").one().status == "published"
    )

    rolled = service.rollback_item(db_session, ctx, install.id)
    assert rolled["rolled_back_from"] == "1.1.0"
    assert rolled["rolled_back_to"] == "1.0.0"
    assert db_session.get(LibraryInstall, install.id).installed_version == "1.0.0"
    assert (
        db_session.query(Skill).filter(Skill.version == "1.1.0").one().status == "deprecated"
    )
    assert db_session.query(Skill).filter(Skill.version == "1.0.0").one().status == "published"
    assert db_session.get(LibraryInstall, install.id).auto_update is False
    audit_actions = {row.action for row in db_session.query(AuditLog).all()}
    assert "library_item_installed" in audit_actions
    assert "library_item_rolled_back" in audit_actions


def test_auto_update_low_risk_only_and_pin_respected(db_session):
    source_id = _make_source(db_session)
    codes = {"demo_low": "low", "demo_medium": "medium", "demo_pinned": "low"}
    v1 = {code: _build(code, "1.0.0", risk) for code, risk in codes.items()}
    routes = {
        MANIFEST_URL: _json_response(
            _manifest_body(
                1,
                [
                    _entry(code, "1.0.0", data, codes[code])
                    for code, data in v1.items()
                ],
            )
        )
    }
    for code, data in v1.items():
        routes[f"https://library.test/packages/{code}-1.0.0.kjxpack"] = _binary_response(data)
    fetcher = FakeFetcher(routes)
    service.sync_source(db_session, YARD, source_id, fetcher)
    ctx = _ctx()
    installs = {}
    for item in db_session.query(LibraryItem).all():
        result = service.install_item(db_session, ctx, item.id, fetcher=fetcher)
        installs[item.item_key] = result["install"]["id"]
    service.pin_item(db_session, ctx, installs["official:skill:demo_pinned"], pinned=True)

    v2 = {code: _build(code, "1.1.0", risk) for code, risk in codes.items()}
    routes[MANIFEST_URL] = _json_response(
        _manifest_body(
            2,
            [_entry(code, "1.1.0", data, codes[code]) for code, data in v2.items()],
        )
    )
    for code, data in v2.items():
        routes[f"https://library.test/packages/{code}-1.1.0.kjxpack"] = _binary_response(data)
    service.sync_source(db_session, YARD, source_id, fetcher)

    outcome = service.apply_auto_updates(db_session, YARD, fetcher)
    assert [row["item_key"] for row in outcome["updated"]] == ["official:skill:demo_low"]
    assert [row["item_key"] for row in outcome["prompted"]] == ["official:skill:demo_medium"]
    versions = {
        row.item_key: row.installed_version for row in db_session.query(LibraryInstall).all()
    }
    assert versions["official:skill:demo_low"] == "1.1.0"
    assert versions["official:skill:demo_medium"] == "1.0.0"
    assert versions["official:skill:demo_pinned"] == "1.0.0"


def test_offline_status_and_source_permissions(db_session):
    yard_ctx = _ctx()
    with pytest.raises(AppError) as exc:
        service.create_source(
            db_session,
            yard_ctx,
            {"name": "越权官方源", "kind": "official", "base_url": "https://x.test/"},
            is_platform_admin=False,
        )
    assert exc.value.code == "LIBRARY_FORBIDDEN"

    created = service.create_source(
        db_session,
        yard_ctx,
        {
            "name": "本小院私有源",
            "kind": "private",
            "base_url": "https://mirror.test/lib/",
            "public_key_b64": PUBLIC,
        },
        is_platform_admin=False,
    )
    assert created["yard_id"] == YARD
    state = service.status(db_session, yard_ctx)
    assert state["offline"] is True
    assert state["installed"] == 0
    assert state["sources"][0]["key_id"] == crypto.public_key_fingerprint(PUBLIC)["key_id"]
    assert "public_key_b64" not in state["sources"][0]

    # 离线/不可达：同步抛业务错误并落账，已安装数据不受影响
    def broken(url: str, headers: dict):
        raise AppError("LIBRARY_SYNC_FAILED", "官方库源暂不可达。", http_status=502)

    with pytest.raises(AppError):
        service.sync_source(db_session, YARD, created["id"], broken)
    source = db_session.get(LibrarySource, created["id"])
    assert source.status == "error"
    assert service.list_installs(db_session, yard_ctx)["total"] == 0

    outcome = service.run_due_library_syncs(db_session, broken)
    assert outcome["processed"] >= 1
    assert all(result["status"] in ("error", "skipped") for result in outcome["results"])


def test_install_rejects_tampered_download(db_session):
    source_id = _make_source(db_session)
    data = _build("demo_skill", "1.0.0")
    tampered = data[:100] + bytes([data[100] ^ 0xFF]) + data[101:]
    routes = {
        MANIFEST_URL: _json_response(_manifest_body(1, [_entry("demo_skill", "1.0.0", data)])),
        "https://library.test/packages/demo_skill-1.0.0.kjxpack": _binary_response(tampered),
    }
    fetcher = FakeFetcher(routes)
    service.sync_source(db_session, YARD, source_id, fetcher)
    item = db_session.query(LibraryItem).one()
    with pytest.raises(AppError) as exc:
        service.install_item(db_session, _ctx(), item.id, fetcher=fetcher)
    assert exc.value.code == "LIBRARY_CHECKSUM_MISMATCH"
    assert db_session.query(Skill).count() == 0


def test_plugin_install_stays_pending_review_and_updates_in_place(db_session):
    source_id = _make_source(db_session)
    v1 = _build_plugin("1.0.0")
    v2 = _build_plugin("1.1.0")
    routes = {
        MANIFEST_URL: _json_response(_manifest_body(1, [_plugin_entry("1.0.0", v1)])),
        "https://library.test/packages/demo_plugin-1.0.0.kjxpack": _binary_response(v1),
        "https://library.test/packages/demo_plugin-1.1.0.kjxpack": _binary_response(v2),
    }
    fetcher = FakeFetcher(routes)
    service.sync_source(db_session, YARD, source_id, fetcher)
    item = db_session.query(LibraryItem).one()
    ctx = _ctx()

    installed = service.install_item(db_session, ctx, item.id, fetcher=fetcher)
    assert installed["plugin_id"] == "demo_plugin"
    record = db_session.query(PluginManifest).one()
    assert record.status == "pending_review"
    assert record.yard_id == YARD
    install = db_session.query(LibraryInstall).one()
    assert install.plugin_id == "demo_plugin"

    routes[MANIFEST_URL] = _json_response(
        _manifest_body(2, [_plugin_entry("1.1.0", v2)])
    )
    service.sync_source(db_session, YARD, source_id, fetcher)
    updated = service.update_item(db_session, ctx, install.id, fetcher=fetcher)
    assert updated["updated"] is True
    assert db_session.query(PluginManifest).count() == 1
    record = db_session.query(PluginManifest).one()
    assert record.version == "1.1.0"
    assert record.status == "pending_review"
    assert db_session.get(LibraryInstall, install.id).previous_version == "1.0.0"
