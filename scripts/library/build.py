#!/usr/bin/env python3
"""构建 .kjxpack（离线、不依赖数据库）。

用法：
  python scripts/library/build.py --kind skill --source ./demo_skill \
      --code demo_skill --version 1.0.0 --out ./packages
  python scripts/library/build.py --kind plugin --source ./demo_plugin/plugin.json \
      --code demo.plugin --version 1.0.0 --out ./packages

私钥来源：--key-file 或环境变量 LIBRARY_SIGNING_SEED（base64url 32 字节）。
脚本只打印公钥指纹/包摘要，绝不打印私钥。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.modules.library import crypto, package  # noqa: E402


def _load_seed(args: argparse.Namespace) -> bytes:
    if args.key_file:
        text = Path(args.key_file).read_text(encoding="utf-8").strip()
    else:
        text = (os.environ.get("LIBRARY_SIGNING_SEED") or "").strip()
    if not text:
        raise SystemExit("缺少签名私钥：请传 --key-file 或设置 LIBRARY_SIGNING_SEED。")
    try:
        return crypto.seed_from_text(text)
    except ValueError as e:
        raise SystemExit(str(e)) from e


def _definition_path(kind: str, source: Path) -> Path:
    if source.is_dir():
        return source / ("definition.json" if kind == "skill" else "plugin.json")
    return source


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建官方技能/插件包 .kjxpack")
    parser.add_argument("--kind", required=True, choices=["skill", "plugin"])
    parser.add_argument("--source", required=True, help="定义文件或包含定义文件的目录")
    parser.add_argument("--code", required=True, help="条目 code（skill code / plugin_id）")
    parser.add_argument("--version", required=True)
    parser.add_argument("--name", default="", help="展示名；缺省取定义中的 name")
    parser.add_argument("--channel", default="stable", choices=["stable", "beta"])
    parser.add_argument("--category", default="other")
    parser.add_argument("--risk-level", default="low", choices=["low", "medium", "high"])
    parser.add_argument("--license", default="")
    parser.add_argument("--author", default="")
    parser.add_argument("--min-app-version", default=None)
    parser.add_argument("--max-app-version", default=None)
    parser.add_argument("--out", default="packages", help="输出目录")
    parser.add_argument("--key-file", default=None, help="base64url 种子文件（不落日志）")
    args = parser.parse_args(argv)

    payload_path = _definition_path(args.kind, Path(args.source).resolve())
    if not payload_path.is_file():
        raise SystemExit(f"未找到定义文件：{payload_path}")
    try:
        definition = json.loads(payload_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"定义不是合法 JSON：{e}") from e
    seed = _load_seed(args)
    public_key = crypto.public_key_b64_from_seed(seed)
    name = args.name or str(definition.get("name") or args.code)
    manifest = {
        "kind": args.kind,
        "item_type": args.kind,
        "code": args.code,
        "name": name,
        "version": args.version,
        "channel": args.channel,
        "description": str(definition.get("description") or definition.get("summary") or "")[:4000],
        "category": args.category,
        "risk_level": args.risk_level,
        "license": args.license or str(definition.get("license") or "")[:50],
        "author": args.author,
        "min_app_version": args.min_app_version,
        "max_app_version": args.max_app_version,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    try:
        data = package.build_package(manifest=manifest, definition=definition, seed=seed)
        # 自检：用同一格式重新验签/校验，防止发布出无法安装的包
        loaded = package.load_package(data, public_key)
    except Exception as e:  # AppError / ValueError 都在此收口为可读退出码
        raise SystemExit(f"构建失败：{getattr(e, 'message', e)}") from e

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{args.code}-{args.version}.kjxpack"
    target.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    print(json.dumps(
        {
            "path": str(target),
            "size_bytes": len(data),
            "sha256": digest,
            "kind": loaded["kind"],
            "code": args.code,
            "version": args.version,
            "key_id": loaded["key_id"],
            "verified": True,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
