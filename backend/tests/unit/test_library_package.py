"""官方包格式与安全解压：正常包、路径穿越、符号链接、超大、checksums、类型规则。"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from app.core.errors import AppError
from app.modules.library import crypto, package

SEED = bytes(range(32))
PUBLIC = crypto.public_key_b64_from_seed(SEED)


def _skill_definition(**overrides) -> dict:
    definition = {
        "code": "demo_skill",
        "name": "演示技能",
        "version": "1.0.0",
        "category": "other",
        "summary": "演示技能摘要",
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
        "guardrails": {"risk_level": "low", "max_steps": 5, "require_citation": False},
        "output_format": "markdown",
        "sources": [],
        "tags": [],
        "license": "内部原创",
    }
    definition.update(overrides)
    return definition


def _plugin_definition(**overrides) -> dict:
    definition = {
        "plugin_id": "demo.plugin",
        "name": "演示插件",
        "version": "1.0.0",
        "type": "workflow",
        "api_compatibility": "1.0",
        "dependencies": [],
        "permissions": [],
        "entry_point": None,
        "config_schema": {},
        "checksum": "",
        "description": "声明式演示插件",
        "steps": [{"kind": "llm", "prompt": "根据 {{question}} 给出建议"}],
    }
    definition.update(overrides)
    return definition


def _build_skill(definition: dict | None = None, *, code: str = "demo_skill") -> bytes:
    return package.build_package(
        manifest={
            "kind": "skill",
            "code": code,
            "name": "演示技能",
            "version": "1.0.0",
            "channel": "stable",
        },
        definition=definition or _skill_definition(),
        seed=SEED,
    )


def _build_plugin(definition: dict | None = None, *, code: str = "demo.plugin") -> bytes:
    return package.build_package(
        manifest={"kind": "plugin", "code": code, "name": "演示插件", "version": "1.0.0"},
        definition=definition or _plugin_definition(),
        seed=SEED,
    )


def _zip(entries: list[tuple[str, bytes, int]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body, mode in entries:
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (mode << 16) if mode else (0o100644 << 16)
            archive.writestr(info, body)
    return buffer.getvalue()


def _replace_entry(data: bytes, name: str, body: bytes) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(data))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            payload = body if info.filename == name else source.read(info.filename)
            target.writestr(info, payload)
    return buffer.getvalue()


def test_load_valid_skill_package():
    loaded = package.load_package(_build_skill(), PUBLIC)
    assert loaded["kind"] == "skill"
    assert loaded["payload_file"] == "definition.json"
    assert set(loaded["files"]) == {
        "manifest.json",
        "checksums.json",
        "SIGNATURE",
        "definition.json",
    }
    assert loaded["definition"]["code"] == "demo_skill"


def test_load_valid_plugin_package():
    loaded = package.load_package(_build_plugin(), PUBLIC)
    assert loaded["kind"] == "plugin"
    assert loaded["payload_file"] == "plugin.json"


def test_reject_path_traversal():
    data = _zip([("../evil.json", b"{}", 0o100644)])
    with pytest.raises(AppError) as exc:
        package.extract_package(data)
    assert exc.value.code == "LIBRARY_PACKAGE_UNSAFE"


def test_reject_absolute_path():
    data = _zip([("/etc/passwd", b"x", 0o100644)])
    with pytest.raises(AppError) as exc:
        package.extract_package(data)
    assert exc.value.code == "LIBRARY_PACKAGE_UNSAFE"


def test_reject_traversal_directory_entry():
    data = _zip([("../", b"", 0o040755)])
    with pytest.raises(AppError) as exc:
        package.extract_package(data)
    assert exc.value.code == "LIBRARY_PACKAGE_UNSAFE"


def test_reject_symlink():
    data = _zip([("definition.json", b"{}", 0o120777)])
    with pytest.raises(AppError) as exc:
        package.extract_package(data)
    assert exc.value.code == "LIBRARY_PACKAGE_UNSAFE"
    assert "符号链接" in exc.value.message


def test_reject_compression_bomb():
    data = _zip([("definition.json", b"0" * 2_000_000, 0o100644)])
    with pytest.raises(AppError) as exc:
        package.extract_package(data)
    assert exc.value.code == "LIBRARY_PACKAGE_UNSAFE"
    assert "压缩比" in exc.value.message


def test_reject_too_many_files():
    entries = [(f"f{index}.txt", b"", 0o100644) for index in range(package.MAX_FILES + 1)]
    data = _zip(entries)
    with pytest.raises(AppError) as exc:
        package.extract_package(data)
    assert exc.value.code == "LIBRARY_PACKAGE_UNSAFE"


def test_reject_checksum_mismatch():
    data = _build_skill()
    tampered = _replace_entry(
        data,
        "definition.json",
        json.dumps(_skill_definition(summary="被替换的摘要"), ensure_ascii=False).encode("utf-8"),
    )
    with pytest.raises(AppError) as exc:
        package.load_package(tampered, PUBLIC)
    assert exc.value.code == "LIBRARY_CHECKSUM_MISMATCH"


def test_reject_non_skill_spec_definition():
    bad = _skill_definition()
    bad.pop("prompt")
    data = _build_skill(bad)
    with pytest.raises(AppError) as exc:
        package.load_package(data, PUBLIC)
    assert exc.value.code == "LIBRARY_PACKAGE_INVALID"
    assert "SkillSpec" in exc.value.message


def test_reject_executable_plugin_field():
    definition = _plugin_definition()
    definition["script"] = "run.py"
    data = _build_plugin(definition)
    with pytest.raises(AppError) as exc:
        package.load_package(data, PUBLIC)
    assert exc.value.code == "LIBRARY_PACKAGE_INVALID"
    assert "可执行字段" in exc.value.message


def test_reject_plugin_entry_point():
    data = _build_plugin(_plugin_definition(entry_point="main.py"))
    with pytest.raises(AppError) as exc:
        package.load_package(data, PUBLIC)
    assert exc.value.code == "LIBRARY_PACKAGE_INVALID"


def test_reject_unlisted_extra_file():
    valid = _build_skill()
    source = zipfile.ZipFile(io.BytesIO(valid))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for info in source.infolist():
            archive.writestr(info, source.read(info.filename))
        archive.writestr("extra.bin", b"unlisted")
    with pytest.raises(AppError) as exc:
        package.load_package(buffer.getvalue(), PUBLIC)
    assert exc.value.code == "LIBRARY_PACKAGE_INVALID"
    assert "未登记" in exc.value.message


def test_reject_expired_manifest_is_service_layer():
    """包层不判断有效期（由同步层判）；此处确认包内 manifest 只认 schema_version。"""
    data = _build_skill()
    loaded = package.load_package(data, PUBLIC)
    assert loaded["manifest"]["schema_version"] == 1
