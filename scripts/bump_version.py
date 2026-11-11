#!/usr/bin/env python3
"""一次性把 9 处版本面改到同一版本（唯一事实源：pyproject.toml）。

用法：
    python scripts/bump_version.py 3.77.0
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^3\.\d+\.\d+$")


def _replace(path: str, pattern: str, repl: str, flags: int = 0) -> None:
    file = ROOT / path
    source = file.read_text()
    updated, count = re.subn(pattern, repl, source, count=1, flags=flags)
    if count != 1:
        raise SystemExit(f"未能在 {path} 定位版本字段（pattern={pattern}）")
    file.write_text(updated)
    print(f"  [OK] {path}")


def main() -> int:
    if len(sys.argv) != 2 or not SEMVER.match(sys.argv[1]):
        print("用法：python scripts/bump_version.py 3.x.y（仅允许 3.x 小版本）", file=sys.stderr)
        return 2
    version = sys.argv[1]
    print(f"版本 → {version}")

    _replace("pyproject.toml", r'(?m)^version = "[^"]+"', f'version = "{version}"')
    _replace(
        "uv.lock",
        r'(?ms)(name = "tech-yard-platform"\nversion = ")[^"]+(")',
        rf"\g<1>{version}\g<2>",
    )
    _replace(
        "client/desktop/src-tauri/Cargo.toml",
        r'(?ms)(\[package\][^\[]*?\nversion = ")[^"]+(")',
        rf"\g<1>{version}\g<2>",
    )
    _replace(
        "client/desktop/src-tauri/Cargo.lock",
        r'(?ms)(name = "techyard-desktop"\nversion = ")[^"]+(")',
        rf"\g<1>{version}\g<2>",
    )
    _replace(
        "client/desktop/src-tauri/tauri.conf.json",
        r'"version": "[^"]+"',
        f'"version": "{version}"',
    )
    _replace("client/desktop/package.json", r'"version": "[^"]+"', f'"version": "{version}"')
    _replace("frontend/package.json", r'"version": "[^"]+"', f'"version": "{version}"')
    _replace("backend/app/main.py", r'FastAPI\(([^)]*?)version="[^"]+"', rf'FastAPI(\g<1>version="{version}"', flags=re.S)
    _replace("contracts/openapi.yaml", r"(?m)^  version: \S+", f"  version: {version}")

    print("完成，请运行 scripts/check_versions.py 复核")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
