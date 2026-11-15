#!/usr/bin/env python3
"""版本一致性检查：以根 pyproject.toml 为唯一事实源。

覆盖发布链路会读到的全部版本面，避免「标签是 3.6.1、客户端还是 3.6.0」这类事故。
兼容 Python 3.11+（标准库 tomllib）与 macOS 系统自带 Python 3.9/3.10（正则解析兜底），
确保 GUI Git 客户端与受限沙箱钩子均可直接执行。
用法：
    python scripts/check_versions.py
    python scripts/check_versions.py --tag v3.6.1
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:
    tomllib = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]


def _read_toml_section_version(path: Path, section: str) -> str:
    if tomllib is not None:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        return str(data.get(section, {}).get("version", ""))
    text = path.read_text(encoding="utf-8")
    m = re.search(
        rf"^\[{re.escape(section)}\]\s*\n(?:(?!\[).*\n)*?version\s*=\s*\"([^\"]+)\"",
        text,
        re.M,
    )
    return m.group(1) if m else ""


def _read_lock_pkg_version(path: Path, pkg_name: str) -> str:
    if tomllib is not None:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        return next(
            (
                str(pkg.get("version", ""))
                for pkg in data.get("package", [])
                if pkg.get("name") == pkg_name
            ),
            "",
        )
    text = path.read_text(encoding="utf-8")
    for block in text.split("[[package]]"):
        if re.search(rf'^name\s*=\s*"{re.escape(pkg_name)}"', block, re.M):
            m = re.search(r'^version\s*=\s*"([^"]+)"', block, re.M)
            if m:
                return m.group(1)
    return ""


def _collect() -> dict[str, str]:
    source = _read_toml_section_version(ROOT / "pyproject.toml", "project")
    uv_version = _read_lock_pkg_version(ROOT / "uv.lock", "tech-yard-platform")
    cargo_version = _read_toml_section_version(
        ROOT / "client/desktop/src-tauri/Cargo.toml", "package"
    )
    cargo_lock_version = _read_lock_pkg_version(
        ROOT / "client/desktop/src-tauri/Cargo.lock", "techyard-desktop"
    )

    tauri = json.loads((ROOT / "client/desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    desktop_pkg = json.loads((ROOT / "client/desktop/package.json").read_text(encoding="utf-8"))
    web_pkg = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))

    main_py = (ROOT / "backend/app/main.py").read_text(encoding="utf-8")
    app_version = re.search(r'FastAPI\([^)]*version="([^"]+)"', main_py, re.S)
    openapi_version = re.search(
        r"^info:\s*\n(?:.*\n)*?\s+version:\s*(\S+)",
        (ROOT / "contracts/openapi.yaml").read_text(encoding="utf-8"),
        re.M,
    )

    return {
        "pyproject.toml": source,
        "uv.lock": uv_version,
        "client/desktop/src-tauri/Cargo.toml": cargo_version,
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
