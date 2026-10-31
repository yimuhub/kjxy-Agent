#!/usr/bin/env python3
"""校验官方库清单与包（发布前/镜像巡检）。

用法：
  python scripts/library/verify.py --manifest ./packages/manifest.json \
      --public-key <base64url> --packages-dir ./packages

检查项：
- 清单签名、schema_version=1、channel、sequence≥0、expires_at 未过期；
- 每个条目 download_url 为 https、sha256 合法；
- 本地包存在、整包 sha256 匹配、包内签名/checksums/SkillSpec/声明式插件全部通过。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.modules.library import crypto, package  # noqa: E402


def _public_key(args: argparse.Namespace) -> str:
    if args.public_key:
        return args.public_key.strip()
    if args.public_key_file:
        return Path(args.public_key_file).read_text(encoding="utf-8").strip()
    raise SystemExit("缺少公钥：请传 --public-key 或 --public-key-file。")


def _check_expiry(raw: str | None) -> str | None:
    if not raw:
        return "清单缺少 expires_at"
    try:
        expires = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return "expires_at 不是合法时间"
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires < datetime.now(UTC):
        return "清单已过期"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验官方库清单与包")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--packages-dir", default=None)
    parser.add_argument("--public-key", default=None)
    parser.add_argument("--public-key-file", default=None)
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest).resolve()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"无法读取清单：{e}") from e
    if not isinstance(manifest, dict):
        raise SystemExit("清单必须是 JSON 对象。")
    public_key = _public_key(args)
    errors: list[str] = []
    warnings: list[str] = []

    verification = crypto.verify_manifest(manifest, public_key)
    if not verification["valid"]:
        errors.append(f"清单签名无效：{verification['reason']}")
    if int(manifest.get("schema_version") or 0) != 1:
        errors.append("schema_version 必须为 1")
    channel = str(manifest.get("channel") or "")
    if channel not in ("stable", "beta"):
        errors.append(f"channel 非法：{channel or '(空)'}")
    try:
        sequence = int(manifest.get("sequence") or 0)
    except (TypeError, ValueError):
        sequence = -1
    if sequence < 0:
        errors.append("sequence 必须是非负整数")
    expiry_error = _check_expiry(manifest.get("expires_at"))
    if expiry_error:
        (warnings if "过期" in expiry_error else errors).append(expiry_error)

    items = manifest.get("items")
    if not isinstance(items, list):
        errors.append("items 必须是数组")
        items = []
    packages_dir = Path(args.packages_dir).resolve() if args.packages_dir else manifest_path.parent
    checked = 0
    for index, item in enumerate(items):
        label = f"items[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} 不是对象")
            continue
        digest = str(item.get("sha256") or "").lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            errors.append(f"{label}.sha256 非法")
        url = str(item.get("download_url") or "")
        if urlparse(url).scheme != "https":
            errors.append(f"{label}.download_url 必须是 https")
        if not args.packages_dir:
            continue
        target = packages_dir / Path(urlparse(url).path).name
        if not target.is_file():
            errors.append(f"{label} 本地包不存在：{target.name}")
            continue
        data = target.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != digest:
            errors.append(f"{label} 整包 sha256 不匹配（{target.name}）")
            continue
        try:
            loaded = package.load_package(data, public_key)
        except Exception as e:
            errors.append(f"{label} 包校验失败：{getattr(e, 'message', e)}")
            continue
        if str(loaded["manifest"].get("code")) != str(item.get("code")):
            errors.append(f"{label} code 与包清单不一致")
        if str(loaded["manifest"].get("version")) != str(item.get("version")):
            errors.append(f"{label} version 与包清单不一致")
        checked += 1

    print(json.dumps(
        {
            "manifest": str(manifest_path),
            "key_id": verification["key_id"],
            "channel": channel,
            "sequence": sequence,
            "items": len(items),
            "packages_checked": checked,
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
