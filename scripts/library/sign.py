#!/usr/bin/env python3
"""Ed25519 签名/验签工具（官方库发布者密钥）。

用法：
  python scripts/library/sign.py keygen --out publisher.seed
  python scripts/library/sign.py sign --file manifest.json --key-file publisher.seed
  python scripts/library/sign.py verify --file manifest.json --public-key <base64url>

- 私钥只从 --key-file 或 LIBRARY_SIGNING_SEED 读取，任何分支都不打印它；
- 签名对象：去掉 signature 字段后的 canonical JSON。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.modules.library import crypto  # noqa: E402


def _load_seed(key_file: str | None) -> bytes:
    if key_file:
        text = Path(key_file).read_text(encoding="utf-8").strip()
    else:
        text = (os.environ.get("LIBRARY_SIGNING_SEED") or "").strip()
    if not text:
        raise SystemExit("缺少私钥：请传 --key-file 或设置 LIBRARY_SIGNING_SEED。")
    return crypto.seed_from_text(text)


def _load_public_key(args: argparse.Namespace) -> str:
    if args.public_key:
        return args.public_key.strip()
    if args.public_key_file:
        return Path(args.public_key_file).read_text(encoding="utf-8").strip()
    raise SystemExit("缺少公钥：请传 --public-key 或 --public-key-file。")


def _read_json(path: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"无法读取 JSON：{e}") from e
    if not isinstance(value, dict):
        raise SystemExit("清单必须是 JSON 对象。")
    return value


def cmd_keygen(args: argparse.Namespace) -> int:
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key = crypto.public_key_b64_from_seed(seed)
    target = Path(args.out)
    target.write_text(crypto.b64url_encode(seed) + "\n", encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:
        pass
    print(json.dumps(
        {
            "seed_file": str(target),
            "public_key_b64": public_key,
            "key_id": crypto.public_key_fingerprint(public_key)["key_id"],
            "note": "私钥已写入文件，终端不显示；请离线备份。",
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


def cmd_sign(args: argparse.Namespace) -> int:
    manifest = _read_json(args.file)
    seed = _load_seed(args.key_file)
    signed = crypto.sign_manifest(manifest, seed)
    target = Path(args.out or args.file)
    target.write_text(json.dumps(signed, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(
        {
            "path": str(target),
            "alg": crypto.ALGORITHM,
            "key_id": signed["key_id"],
            "verified": crypto.verify_manifest(
                signed, crypto.public_key_b64_from_seed(seed)
            )["valid"],
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    manifest = _read_json(args.file)
    public_key = _load_public_key(args)
    result = crypto.verify_manifest(manifest, public_key)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="官方库 Ed25519 签名/验签")
    sub = parser.add_subparsers(dest="command", required=True)

    keygen = sub.add_parser("keygen", help="生成发布者密钥（只打印公钥）")
    keygen.add_argument("--out", required=True, help="私钥种子文件路径")
    keygen.set_defaults(func=cmd_keygen)

    sign = sub.add_parser("sign", help="给清单/checksums 对象签名")
    sign.add_argument("--file", required=True)
    sign.add_argument("--out", default=None, help="输出文件；缺省原地覆盖")
    sign.add_argument("--key-file", default=None)
    sign.set_defaults(func=cmd_sign)

    verify = sub.add_parser("verify", help="验证清单签名")
    verify.add_argument("--file", required=True)
    verify.add_argument("--public-key", default=None)
    verify.add_argument("--public-key-file", default=None)
    verify.set_defaults(func=cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
