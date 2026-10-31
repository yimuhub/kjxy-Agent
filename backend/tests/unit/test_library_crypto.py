"""官方库签名：有效/篡改/缺签名/错公钥/算法与校验和。"""

from __future__ import annotations

import hashlib

import pytest
from app.core.errors import AppError
from app.modules.library import crypto, package

SEED = bytes(range(32))
OTHER_SEED = bytes(range(1, 33))


def _public(seed: bytes = SEED) -> str:
    return crypto.public_key_b64_from_seed(seed)


def _manifest() -> dict:
    return {
        "schema_version": 1,
        "kind": "skill",
        "code": "demo_skill",
        "name": "演示技能",
        "version": "1.0.0",
        "channel": "stable",
        "sequence": 3,
        "items": [{"code": "demo_skill", "version": "1.0.0"}],
    }


def test_canonical_json_sorts_and_keeps_chinese():
    text = crypto.canonical_json({"b": 1, "a": "中文"})
    assert text == '{"a":"中文","b":1}'


def test_verify_signed_manifest_ok():
    signed = crypto.sign_manifest(_manifest(), SEED)
    result = crypto.verify_manifest(signed, _public())
    assert result["valid"] is True
    assert result["reason"] == "ok"
    assert result["key_id"] == crypto.public_key_fingerprint(_public())["key_id"]


def test_tampered_manifest_is_rejected():
    signed = crypto.sign_manifest(_manifest(), SEED)
    signed["name"] = "被改过的名字"
    assert crypto.verify_manifest(signed, _public()) == {
        "valid": False,
        "key_id": crypto.public_key_fingerprint(_public())["key_id"],
        "reason": "bad_signature",
    }


def test_missing_signature():
    result = crypto.verify_manifest(_manifest(), _public())
    assert result["valid"] is False
    assert result["reason"] == "missing_signature"


def test_wrong_public_key():
    signed = crypto.sign_manifest(_manifest(), SEED)
    result = crypto.verify_manifest(signed, _public(OTHER_SEED))
    assert result["valid"] is False
    assert result["reason"] == "bad_signature"


def test_unsupported_alg():
    signed = crypto.sign_manifest(_manifest(), SEED)
    signed["alg"] = "rsa"
    result = crypto.verify_manifest(signed, _public())
    assert result["valid"] is False
    assert result["reason"] == "unsupported_alg"


def test_verify_checksums_full_match_and_mismatch():
    files = {"definition.json": b'{"a":1}', "plugin.json": b"{}"}
    checksums = {
        "algorithm": "sha256",
        "files": {name: hashlib.sha256(body).hexdigest() for name, body in files.items()},
    }
    assert crypto.verify_checksums(files, checksums)["valid"] is True
    checksums["files"]["definition.json"] = "0" * 64
    result = crypto.verify_checksums(files, checksums)
    assert result["valid"] is False
    assert result["mismatched"] == ["definition.json"]


def test_verify_checksums_missing_file():
    result = crypto.verify_checksums({}, {"files": {"definition.json": "0" * 64}})
    assert result["valid"] is False
    assert result["missing"] == ["definition.json"]


def test_package_signature_and_digest_roundtrip():
    definition = {
        "code": "demo_skill",
        "name": "演示技能",
        "version": "1.0.0",
        "category": "other",
        "summary": "演示",
        "when_to_use": "需要演示时",
        "steps": [{"kind": "prompt", "title": "整理", "text": "把事实整理成条目"}],
        "knowledge_scopes": ["yard"],
        "prompt": "请把用户给出的事实整理成三条可执行建议，并标注不确定项。",
        "eval_cases": [
            {"input": "整理灌溉记录", "expect": "输出三条建议"},
            {"input": "没有数据", "expect": "列出待补数据"},
        ],
        "guardrails": {"risk_level": "low", "max_steps": 5, "require_citation": False},
        "license": "内部原创",
    }
    data = package.build_package(
        manifest={"kind": "skill", "code": "demo_skill", "name": "演示技能", "version": "1.0.0"},
        definition=definition,
        seed=SEED,
    )
    loaded = package.load_package(data, _public())
    assert loaded["kind"] == "skill"
    assert loaded["manifest"]["code"] == "demo_skill"
    assert loaded["sha256"] == hashlib.sha256(data).hexdigest()


def test_package_rejects_wrong_public_key():
    definition = {
        "code": "demo_skill",
        "name": "演示技能",
        "version": "1.0.0",
        "category": "other",
        "summary": "演示",
        "when_to_use": "需要演示时",
        "steps": [{"kind": "prompt", "text": "整理事实"}],
        "knowledge_scopes": ["yard"],
        "prompt": "请把用户给出的事实整理成三条可执行建议，并标注不确定项。",
        "eval_cases": [{"input": "a", "expect": "b"}, {"input": "c", "expect": "d"}],
        "guardrails": {"risk_level": "low", "max_steps": 5, "require_citation": False},
        "license": "内部原创",
    }
    data = package.build_package(
        manifest={"kind": "skill", "code": "demo_skill", "name": "演示技能", "version": "1.0.0"},
        definition=definition,
        seed=SEED,
    )
    with pytest.raises(AppError) as exc:
        package.load_package(data, _public(OTHER_SEED))
    assert exc.value.code == "LIBRARY_SIGNATURE_INVALID"
