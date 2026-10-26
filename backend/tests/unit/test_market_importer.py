"""本地导入：Skill 文本解析、待审落地、zip 安全校验与冲突后缀。"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.evolution.models import Skill
from app.modules.market.importer import import_plugin_zip, import_skill_file
from app.modules.market.models import MarketItem
from app.modules.plugins.models import PluginManifest

CTX = YardContext(
    yard_id="yard-importer", user_id="u-importer", roles=["yard_admin"], request_id="req"
)

SKILL_MD = """---
name: 土壤快检助手
description: 整理土壤快检结果并给出复测建议。
metadata:
  version: "1.2.0"
license: MIT
---
# 土壤快检助手

## Purpose

帮助整理土壤检测结果并识别限制因子。

## Instructions

1. 收集检测报告与地块信息。
2. 按 pH、有机质、氮磷钾逐项判读。
3. 给出复测清单，不编造检测数值。
"""


def _manifest(**overrides) -> dict:
    data = {
        "plugin_id": "demo-pack",
        "name": "演示知识包",
        "version": "1.0.0",
        "type": "knowledge_pack",
        "api_compatibility": "1.0",
        "dependencies": [],
        "permissions": [],
        "entry_point": None,
        "config_schema": {},
        "checksum": "",
    }
    data.update(overrides)
    return data


def _zip(entries: dict[str, bytes | str], *, symlink: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            archive.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = 0o120777 << 16
            archive.writestr(info, "")
    return buffer.getvalue()


def test_import_skill_file_creates_pending_skill(db_session):
    result = import_skill_file(db_session, CTX, "soil-check.md", SKILL_MD)
    assert result["item_key"] == "local:skill:soil-check"
    assert result["code"] == "soil-check"
    assert result["skill_status"] == "pending_review"
    assert result["verified"] is False

    item = db_session.query(MarketItem).filter(MarketItem.item_key == result["item_key"]).one()
    assert item.source == "local"
    assert item.item_type == "skill"
    assert item.status == "available"
    assert item.verified is False
    assert item.definition_json["name"] == "土壤快检助手"
    assert "不编造" in item.definition_json["prompt"]
    assert item.definition_json["license"] == "MIT"

    skill = db_session.query(Skill).filter(Skill.skill_code == "soil-check").one()
    assert skill.yard_id == CTX.yard_id
    assert skill.status == "pending_review"
    assert skill.created_from == "market_import"
    assert skill.version == "1.2.0"


def test_import_skill_ignores_self_declared_verified(db_session):
    # 文件内自带 builtin/verified 标记不可信，不能绕过专家审核
    text = "---\nname: 已验证技能\nbuiltin: true\nverified: true\n---\n# 已验证技能\n\n正文说明。"
    result = import_skill_file(db_session, CTX, "verified-skill.txt", text)
    assert result["verified"] is False
    assert result["skill_status"] == "pending_review"
    skill = db_session.query(Skill).filter(Skill.skill_code == "verified-skill").one()
    assert skill.status == "pending_review"
    assert skill.approved_by is None


def test_import_skill_verified_false_stays_pending(db_session):
    text = "---\nname: 未核验技能\nverified: false\n---\n# 未核验技能\n\n正文说明。"
    result = import_skill_file(db_session, CTX, "unverified-skill.md", text)
    assert result["verified"] is False
    assert result["skill_status"] == "pending_review"


def test_import_skill_conflict_suffix(db_session):
    first = import_skill_file(db_session, CTX, "soil-check.md", SKILL_MD)
    second = import_skill_file(db_session, CTX, "soil-check.md", SKILL_MD)
    assert first["code"] == "soil-check"
    assert second["code"] == "soil-check-2"
    assert second["item_key"] == "local:skill:soil-check-2"
    assert db_session.query(Skill).count() == 2


def test_import_skill_rejects_bad_type_and_oversize(db_session):
    with pytest.raises(AppError) as err:
        import_skill_file(db_session, CTX, "skill.pdf", b"x")
    assert err.value.code == "IMPORT_TYPE_UNSUPPORTED"

    with pytest.raises(AppError) as err:
        import_skill_file(db_session, CTX, "big.md", b"x" * (256 * 1024 + 1))
    assert err.value.code == "IMPORT_TOO_LARGE"
    assert err.value.http_status == 413


def test_import_plugin_zip_pending_review(db_session):
    manifest = _manifest()
    data = _zip({"manifest.json": json.dumps(manifest), "README.md": "# demo"})
    result = import_plugin_zip(db_session, CTX, "demo.zip", data)
    assert result["plugin_id"] == "demo-pack"
    assert result["plugin_status"] == "pending_review"
    assert result["checksum_ok"] is True

    record = db_session.query(PluginManifest).filter(PluginManifest.plugin_id == "demo-pack").one()
    assert record.status == "pending_review"
    assert record.yard_id == CTX.yard_id
    assert record.manifest_json["name"] == "演示知识包"
    item = db_session.query(MarketItem).filter(MarketItem.item_type == "plugin").one()
    assert item.source == "local"
    assert item.manifest_json["plugin_id"] == "demo-pack"


def test_import_plugin_zip_conflict_suffix(db_session):
    data = _zip({"manifest.json": json.dumps(_manifest()), "README.md": "demo"})
    first = import_plugin_zip(db_session, CTX, "demo.zip", data)
    second = import_plugin_zip(db_session, CTX, "demo.zip", data)
    assert first["plugin_id"] == "demo-pack"
    assert second["plugin_id"] == "demo-pack-2"
    assert db_session.query(PluginManifest).count() == 2


@pytest.mark.parametrize("name", ["../evil.md", "/etc/passwd.md", "scripts/run.sh", "data.bin"])
def test_import_plugin_zip_rejects_unsafe_entries(db_session, name):
    data = _zip({"manifest.json": json.dumps(_manifest()), name: "payload"})
    with pytest.raises(AppError):
        import_plugin_zip(db_session, CTX, "bad.zip", data)
    assert db_session.query(PluginManifest).count() == 0


def test_import_plugin_zip_rejects_symlink(db_session):
    data = _zip({"manifest.json": json.dumps(_manifest())}, symlink="link.md")
    with pytest.raises(AppError) as err:
        import_plugin_zip(db_session, CTX, "bad.zip", data)
    assert err.value.code == "PLUGIN_IMPORT_UNSAFE"


def test_import_plugin_zip_requires_manifest(db_session):
    data = _zip({"README.md": "# no manifest"})
    with pytest.raises(AppError) as err:
        import_plugin_zip(db_session, CTX, "bad.zip", data)
    assert err.value.code == "PLUGIN_MANIFEST_INVALID"


def test_import_plugin_zip_rejects_invalid_manifest(db_session):
    manifest = _manifest(type="unknown_type")
    data = _zip({"manifest.json": json.dumps(manifest)})
    with pytest.raises(AppError) as err:
        import_plugin_zip(db_session, CTX, "bad.zip", data)
    assert err.value.code == "PLUGIN_TYPE_UNSUPPORTED"


def test_import_plugin_zip_too_large(db_session):
    data = b"PK" + b"x" * (2 * 1024 * 1024 + 1)
    with pytest.raises(AppError) as err:
        import_plugin_zip(db_session, CTX, "big.zip", data)
    assert err.value.code == "IMPORT_TOO_LARGE"
