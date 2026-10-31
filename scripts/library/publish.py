#!/usr/bin/env python3
"""生成官方库清单 manifest.json（schema_version/channel/sequence/items/signature）。

用法：
  python scripts/library/publish.py --packages-dir ./packages \
      --base-url https://library.example.com/stable/ \
      --key-file publisher.seed --channel stable --expires-days 30

- 序号单调：未显式 --sequence 时读取既有 manifest.json 并 +1；
- 每个 .kjxpack 都会先用发布者公钥完整验签/验包，坏包直接拒绝发布；
- 不打印私钥；只输出 key_id、条目数与清单路径。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.modules.library import crypto, package  # noqa: E402


def _load_seed(key_file: str | None) -> bytes:
    if key_file:
        text = Path(key_file).read_text(encoding="utf-8").strip()
    else:
        text = (os.environ.get("LIBRARY_SIGNING_SEED") or "").strip()
    if not text:
        raise SystemExit("缺少私钥：请传 --key-file 或设置 LIBRARY_SIGNING_SEED。")
    return crypto.seed_from_text(text)


def _existing_sequence(out: Path) -> int:
    if not out.is_file():
        return 0
    try:
        value = json.loads(out.read_text(encoding="utf-8"))
        return int(value.get("sequence") or 0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0


def _item_from_package(path: Path, public_key: str, base_url: str) -> dict:
    data = path.read_bytes()
    try:
        loaded = package.load_package(data, public_key)
    except Exception as e:
        raise SystemExit(f"包校验失败（{path.name}）：{getattr(e, 'message', e)}") from e
    manifest = loaded["manifest"]
    filename = path.name
    download_url = base_url.rstrip("/") + "/" + filename if base_url else ""
    return {
        "item_type": loaded["kind"],
        "code": str(manifest["code"]),
        "name": str(manifest.get("name") or manifest["code"]),
        "version": str(manifest["version"]),
        "min_app_version": manifest.get("min_app_version"),
        "max_app_version": manifest.get("max_app_version"),
        "description": str(manifest.get("description") or ""),
        "category": manifest.get("category"),
        "risk_level": str(manifest.get("risk_level") or "low"),
        "license": manifest.get("license"),
        "author": manifest.get("author"),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "download_url": download_url,
        "published_at": manifest.get("generated_at") or datetime.now(UTC).isoformat(),
        "manifest": {k: v for k, v in manifest.items() if k != "signature"},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成并签名官方库清单 manifest.json")
    parser.add_argument("--packages-dir", required=True)
    parser.add_argument("--base-url", default="", help="包下载基地址（https，末尾可带 /）")
    parser.add_argument(
        "--out", default=None, help="输出清单路径；缺省 <packages-dir>/manifest.json"
    )
    parser.add_argument("--channel", default="stable", choices=["stable", "beta"])
    parser.add_argument("--sequence", type=int, default=None)
    parser.add_argument("--expires-days", type=int, default=30)
    parser.add_argument("--publisher", default="科技小院官方")
    parser.add_argument("--removed", default="", help="逗号分隔的撤销 item_key")
    parser.add_argument("--key-file", default=None)
    args = parser.parse_args(argv)

    packages_dir = Path(args.packages_dir).resolve()
    if not packages_dir.is_dir():
        raise SystemExit(f"目录不存在：{packages_dir}")
    if not args.base_url:
        raise SystemExit("--base-url 必填：清单中的 download_url 必须是 https 绝对地址。")
    if not args.base_url.startswith("https://"):
        raise SystemExit("--base-url 必须是 https 地址。")

    seed = _load_seed(args.key_file)
    public_key = crypto.public_key_b64_from_seed(seed)
    out = Path(args.out) if args.out else packages_dir / "manifest.json"
    sequence = args.sequence if args.sequence is not None else _existing_sequence(out) + 1
    now = datetime.now(UTC)
    items = [
        _item_from_package(path, public_key, args.base_url)
        for path in sorted(packages_dir.glob("*.kjxpack"))
    ]
    items.sort(key=lambda item: (item["item_type"], item["code"], item["version"]))
    manifest = {
        "schema_version": 1,
        "channel": args.channel,
        "sequence": sequence,
        "generated_at": now.isoformat(),
        "expires_at": (now + timedelta(days=max(1, args.expires_days))).isoformat(),
        "publisher": {
            "name": args.publisher,
            "key_id": crypto.public_key_fingerprint(public_key)["key_id"],
        },
        "items": items,
    }
    removed = [key.strip() for key in (args.removed or "").split(",") if key.strip()]
    if removed:
        manifest["removed"] = removed
    signed = crypto.sign_manifest(manifest, seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(signed, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(
        {
            "path": str(out),
            "schema_version": 1,
            "channel": args.channel,
            "sequence": sequence,
            "items": len(items),
            "removed": len(removed),
            "key_id": signed["key_id"],
            "verified": crypto.verify_manifest(signed, public_key)["valid"],
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
