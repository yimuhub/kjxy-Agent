"""官方库签名与摘要（v3.35）。

与技能实例签名（``evolution.attestation``）刻意分离：

- 实例签名用本实例密钥，只回答「上架后有没有被改过」；
- 官方库签名用**发布者公钥**验签，回答「这份清单/包是不是该发布者签的」。

规约：
- 规范 JSON：键排序、紧凑分隔、不转义中文（同名同物永远同一串）；
- 清单签名对象：去掉 ``signature`` 字段后的 canonical JSON；
- 包内 ``SIGNATURE``：对 ``checksums.json`` 的 canonical JSON 签名；
- 签名算法只接受 ``ed25519``，签名以 base64url（无填充亦可）编码；
- 公钥指纹 = 公钥原始字节 SHA-256 前 16 位（与技能 key_id 同口径）。

本模块不读应用配置、不触碰实例私钥，CLI 可直接复用。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

ALGORITHM = "ed25519"
SUPPORTED_ALGS = frozenset({ALGORITHM})

REASON_OK = "ok"
REASON_BAD_SIGNATURE = "bad_signature"
REASON_MISSING_SIGNATURE = "missing_signature"
REASON_UNSUPPORTED_ALG = "unsupported_alg"

KEY_ID_LENGTH = 16

__all__ = [
    "ALGORITHM",
    "REASON_BAD_SIGNATURE",
    "REASON_MISSING_SIGNATURE",
    "REASON_OK",
    "REASON_UNSUPPORTED_ALG",
    "b64url_decode",
    "b64url_encode",
    "canonical_json",
    "key_id_for_public_key",
    "public_key_b64_from_seed",
    "public_key_fingerprint",
    "seed_from_text",
    "sign_bytes",
    "sign_canonical",
    "sign_manifest",
    "verify_checksums",
    "verify_detached",
    "verify_manifest",
]


def canonical_json(value: Any) -> str:
    """规范 JSON：键排序、紧凑分隔、不转义中文。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64url_decode(text: str) -> bytes:
    padded = text + "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def seed_from_text(text: str) -> bytes:
    """把 base64url 的 32 字节种子解码成 bytes；不合法时抛 ValueError。"""
    try:
        seed = b64url_decode(text.strip())
    except (ValueError, TypeError, binascii.Error) as e:
        raise ValueError("种子不是合法的 base64url 文本。") from e
    if len(seed) != 32:
        raise ValueError("种子解码后必须是 32 字节（Ed25519 seed）。")
    return seed


def _public_key(raw_public: bytes) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(raw_public)


def key_id_for_public_key(raw_public: bytes) -> str:
    return hashlib.sha256(raw_public).hexdigest()[:KEY_ID_LENGTH]


def public_key_fingerprint(public_key_b64: str) -> dict[str, str]:
    """公钥指纹视图：只暴露 key_id 与算法，绝不回传私钥/原始密钥。"""
    try:
        raw = b64url_decode((public_key_b64 or "").strip())
    except (ValueError, TypeError, binascii.Error):
        return {"alg": ALGORITHM, "key_id": ""}
    if len(raw) != 32:
        return {"alg": ALGORITHM, "key_id": ""}
    return {"alg": ALGORITHM, "key_id": key_id_for_public_key(raw)}


def public_key_b64_from_seed(seed: bytes) -> str:
    key = Ed25519PrivateKey.from_private_bytes(seed)
    raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return b64url_encode(raw)


def sign_bytes(payload: bytes, seed: bytes) -> str:
    """用 32 字节种子签名任意字节串，返回 base64url。"""
    key = Ed25519PrivateKey.from_private_bytes(seed)
    return b64url_encode(key.sign(payload))


def sign_canonical(value: Any, seed: bytes) -> str:
    return sign_bytes(canonical_json(value).encode("utf-8"), seed)


def _signature_value(manifest: dict) -> tuple[str | None, str | None]:
    """兼容 ``signature`` 为字符串或 {alg,value} 对象；返回 (alg, signature)。"""
    raw = manifest.get("signature")
    alg = manifest.get("alg") or manifest.get("signature_alg")
    if isinstance(raw, dict):
        alg = alg or raw.get("alg")
        raw = raw.get("value") or raw.get("signature")
    if isinstance(alg, str):
        alg = alg.strip().lower() or None
    return (alg or ALGORITHM), (str(raw).strip() if isinstance(raw, str) else None)


def verify_detached(payload: bytes, signature_b64: str, public_key_b64: str) -> bool:
    try:
        signature = b64url_decode(signature_b64)
        raw_public = b64url_decode((public_key_b64 or "").strip())
        if len(raw_public) != 32:
            return False
        _public_key(raw_public).verify(signature, payload)
    except (InvalidSignature, ValueError, TypeError, binascii.Error):
        return False
    return True


def verify_manifest(manifest: dict, public_key_b64: str) -> dict[str, Any]:
    """验签清单：返回 {valid, key_id, reason}。

    reason：ok / bad_signature / missing_signature / unsupported_alg。
    """
    key_id = public_key_fingerprint(public_key_b64)["key_id"]
    if not isinstance(manifest, dict) or not manifest:
        return {"valid": False, "key_id": key_id, "reason": REASON_MISSING_SIGNATURE}
    alg, signature = _signature_value(manifest)
    if alg not in SUPPORTED_ALGS:
        return {"valid": False, "key_id": key_id, "reason": REASON_UNSUPPORTED_ALG}
    if not signature:
        return {"valid": False, "key_id": key_id, "reason": REASON_MISSING_SIGNATURE}
    body = {key: value for key, value in manifest.items() if key != "signature"}
    payload = canonical_json(body).encode("utf-8")
    if not verify_detached(payload, signature, public_key_b64):
        return {"valid": False, "key_id": key_id, "reason": REASON_BAD_SIGNATURE}
    return {"valid": True, "key_id": key_id, "reason": REASON_OK}


def sign_manifest(manifest: dict, seed: bytes) -> dict[str, Any]:
    """返回带 alg/key_id/signature 的清单副本（不修改入参）。"""
    body = {key: value for key, value in manifest.items() if key != "signature"}
    body["alg"] = ALGORITHM
    body["key_id"] = key_id_for_public_key(b64url_decode(public_key_b64_from_seed(seed)))
    body["signature"] = sign_canonical(body, seed)
    return body


def verify_checksums(files: dict[str, bytes], checksums: dict) -> dict[str, Any]:
    """校验文件 SHA-256：清单内每个文件都必须存在且摘要一致。

    返回 {valid, reason, mismatched, missing}；额外文件由 package 层负责拒绝，
    避免「签名只覆盖 checksums.json，却夹带未登记文件」。
    """
    if not isinstance(checksums, dict) or not checksums:
        return {"valid": False, "reason": "empty_checksums", "mismatched": [], "missing": []}
    if "files" in checksums and isinstance(checksums["files"], dict):
        entries = checksums["files"]
    else:
        entries = {
            str(key): value
            for key, value in checksums.items()
            if str(key) not in {"algorithm", "generated_at"}
        }
    mismatched: list[str] = []
    missing: list[str] = []
    for name, expected in entries.items():
        content = files.get(name)
        if content is None:
            missing.append(str(name))
            continue
        actual = hashlib.sha256(content).hexdigest()
        if actual != str(expected).strip().lower():
            mismatched.append(str(name))
    if missing or mismatched:
        return {
            "valid": False,
            "reason": "checksum_mismatch",
            "mismatched": mismatched,
            "missing": missing,
        }
    return {"valid": True, "reason": REASON_OK, "mismatched": [], "missing": []}
