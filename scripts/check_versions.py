#!/usr/bin/env python3
"""版本一致性检查：以根 pyproject.toml 为唯一事实源。

覆盖发布链路会读到的全部版本面，避免「标签是 3.6.1、客户端还是 3.6.0」这类事故。
用法：
    python scripts/check_versions.py
    python scripts/check_versions.py --tag v3.6.1
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read_toml(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _collect() -> dict[str, str]:
    pyproject = _read_toml(ROOT / "pyproject.toml")
    source = str(pyproject["project"]["version"])

    uv = _read_toml(ROOT / "uv.lock")
    uv_version = next(
        (
            str(pkg.get("version", ""))
            for pkg in uv.get("package", [])
            if pkg.get("name") == "tech-yard-platform"
        ),
        "",
    )

    cargo = _read_toml(ROOT / "client/desktop/src-tauri/Cargo.toml")
    cargo_lock = _read_toml(ROOT / "client/desktop/src-tauri/Cargo.lock")
    cargo_lock_version = next(
        (
            str(pkg.get("version", ""))
            for pkg in cargo_lock.get("package", [])
            if pkg.get("name") == "techyard-desktop"
        ),
        "",
    )

    tauri = json.loads((ROOT / "client/desktop/src-tauri/tauri.conf.json").read_text())
    desktop_pkg = json.loads((ROOT / "client/desktop/package.json").read_text())
    web_pkg = json.loads((ROOT / "frontend/package.json").read_text())

    main_py = (ROOT / "backend/app/main.py").read_text()
    app_version = re.search(r'FastAPI\([^)]*version="([^"]+)"', main_py, re.S)
    openapi_version = re.search(
        r"^info:\s*\n(?:.*\n)*?\s+version:\s*(\S+)", (ROOT / "contracts/openapi.yaml").read_text(), re.M
    )

    return {
        "pyproject.toml": source,
        "uv.lock": uv_version,
        "client/desktop/src-tauri/Cargo.toml": str(cargo["package"]["version"]),
        "client/desktop/src-tauri/Cargo.lock": cargo_lock_version,
        "client/desktop/src-tauri/tauri.conf.json": str(tauri["version"]),
        "client/desktop/package.json": str(desktop_pkg["version"]),
        "frontend/package.json": str(web_pkg["version"]),
        "backend/app/main.py": app_version.group(1) if app_version else "",
        "contracts/openapi.yaml": openapi_version.group(1) if openapi_version else "",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", help="期望与版本面一致的标签，如 v3.6.1")
    args = parser.parse_args()

    versions = _collect()
    expected = versions["pyproject.toml"]
    if args.tag:
        expected = args.tag.removeprefix("v")

    bad = {path: ver for path, ver in versions.items() if ver != expected}
    if bad or not expected:
        print(f"版本不一致，期望 {expected or '<empty>'}：", file=sys.stderr)
        for path, ver in versions.items():
            mark = "OK" if ver == expected else "!!"
            print(f"  [{mark}] {path}: {ver or '<missing>'}", file=sys.stderr)
        return 1

    print(f"版本一致：{expected}（{len(versions)} 处）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
