"""技能定义摘要与签名（v3.17）。

口径：
- 摘要：definition 规范 JSON（键排序、紧凑分隔、UTF-8）→ SHA-256；
- 签名：Ed25519，签名对象是 ``skill:v1 / code / version / 摘要`` 四段文本；
- 私钥：优先 ``SKILL_SIGNING_SEED``（base64url 的 32 字节种子），未配置时由
  ``APP_SECRET_KEY`` 做域分离派生——同一实例稳定，换密钥后旧签名会如实判为
  「不可验证」，绝不静默当作有效；
- key_id：公钥 SHA-256 前 16 位，用于在密钥轮换后说明是哪把钥匙签的。

签名不是访问控制：它只回答「这份定义在签名之后有没有被改过」。
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.core.config import get_settings

ALGORITHM = "ed25519"
_SEED_DOMAIN = b"kjxy-skill-signing-v1:"
_PAYLOAD_DOMAIN = "skill:v1"


def canonical_json(value: Any) -> str:
    """规范 JSON：键排序、紧凑分隔、不转义中文——同一内容永远同一串。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def definition_digest(definition: dict | None) -> str:
    return hashlib.sha256(canonical_json(definition or {}).encode("utf-8")).hexdigest()


def _decode_seed(raw: str) -> bytes:
    padded = raw + "=" * (-len(raw) % 4)
    try:
        seed = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, TypeError) as e:
        raise RuntimeError("SKILL_SIGNING_SEED 不是合法的 base64url 文本。") from e
    if len(seed) != 32:
        raise RuntimeError("SKILL_SIGNING_SEED 解码后必须是 32 字节（Ed25519 种子）。")
    return seed


def _seed_bytes() -> bytes:
    configured = (get_settings().skill_signing_seed or "").strip()
    if configured:
        return _decode_seed(configured)
    secret = (get_settings().app_secret_key or "").encode("utf-8")
    return hashlib.sha256(_SEED_DOMAIN + secret).digest()


@lru_cache(maxsize=8)
def _keypair(seed_hex: str) -> tuple[Ed25519PrivateKey, bytes]:
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed_hex))
    raw_public = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return key, raw_public


def _current() -> tuple[Ed25519PrivateKey, bytes]:
    return _keypair(_seed_bytes().hex())


def public_key_b64() -> str:
    _, raw = _current()
    return base64.urlsafe_b64encode(raw).decode("ascii")


def key_id() -> str:
    _, raw = _current()
    return hashlib.sha256(raw).hexdigest()[:16]


def signing_payload(skill_code: str, version: str, digest: str) -> bytes:
    return f"{_PAYLOAD_DOMAIN}\n{skill_code}\n{version}\n{digest}".encode()


def sign_definition(*, skill_code: str, version: str, definition: dict) -> dict[str, Any]:
    """返回可直接写入 Skill 行签名字段的字典。"""
    digest = definition_digest(definition)
    key, _ = _current()
    signature = key.sign(signing_payload(skill_code, version, digest))
    return {
        "definition_sha256": digest,
        "signature": base64.urlsafe_b64encode(signature).decode("ascii"),
        "signature_alg": ALGORITHM,
        "key_id": key_id(),
        "signed_at": datetime.now(UTC),
    }


def verify_definition(
    *,
    skill_code: str,
    version: str,
    definition: dict,
    digest: str | None,
    signature: str | None,
    signature_key_id: str | None,
) -> dict[str, Any]:
    """验签：返回 {valid, signed, reason, digest, expected_digest}。

    reason 取值：``ok`` / ``unsigned`` / ``definition_mismatch`` /
    ``bad_signature`` / ``key_mismatch``。
    """
    current_digest = definition_digest(definition)
    if not signature or not digest:
        return {
            "valid": False,
            "signed": False,
            "reason": "unsigned",
            "digest": current_digest,
            "expected_digest": digest,
        }
    if digest != current_digest:
        return {
            "valid": False,
            "signed": True,
            "reason": "definition_mismatch",
            "digest": current_digest,
            "expected_digest": digest,
        }
    if signature_key_id and signature_key_id != key_id():
        return {
            "valid": False,
            "signed": True,
            "reason": "key_mismatch",
            "digest": current_digest,
            "expected_digest": digest,
        }
    try:
        raw_signature = base64.urlsafe_b64decode(signature.encode("ascii"))
        _, raw_public = _current()
        Ed25519PublicKey.from_public_bytes(raw_public).verify(
            raw_signature, signing_payload(skill_code, version, digest)
        )
    except (InvalidSignature, ValueError, TypeError):
        return {
            "valid": False,
            "signed": True,
            "reason": "bad_signature",
            "digest": current_digest,
            "expected_digest": digest,
        }
    return {
        "valid": True,
        "signed": True,
        "reason": "ok",
        "digest": current_digest,
        "expected_digest": digest,
    }


def apply_signature(skill: Any, actor_id: str) -> dict[str, Any]:
    """给 Skill 行写入签名（调用方负责 commit）。返回签名信息。"""
    info = sign_definition(
        skill_code=skill.skill_code,
        version=skill.version,
        definition=skill.definition_json or {},
    )
    skill.definition_sha256 = info["definition_sha256"]
    skill.signature = info["signature"]
    skill.signature_alg = info["signature_alg"]
    skill.key_id = info["key_id"]
    skill.signed_at = info["signed_at"]
    skill.signed_by = actor_id
    return info


def verify_skill(skill: Any) -> dict[str, Any]:
    return verify_definition(
        skill_code=skill.skill_code,
        version=skill.version,
        definition=skill.definition_json or {},
        digest=skill.definition_sha256,
        signature=skill.signature,
        signature_key_id=skill.key_id,
    )


def signing_key_info() -> dict[str, Any]:
    return {
        "alg": ALGORITHM,
        "key_id": key_id(),
        "public_key": public_key_b64(),
        "note": "公钥用于验证技能签名；轮换密钥后旧签名会判为不可验证。",
    }


def report_digest(report: dict) -> str:
    return hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest()


__all__ = [
    "ALGORITHM",
    "apply_signature",
    "canonical_json",
    "definition_digest",
    "key_id",
    "public_key_b64",
    "report_digest",
    "sign_definition",
    "signing_key_info",
    "signing_payload",
    "verify_definition",
    "verify_skill",
]
