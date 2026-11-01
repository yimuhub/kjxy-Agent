#!/usr/bin/env python3
"""前端界面规范检查：弹窗居中、表格列宽、禁止右侧抽屉。

规则来源：根目录 AGENTS.md。CI 与 `make check` 都会执行本脚本。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend" / "src"
DESIGN_SYSTEM = FRONTEND / "styles" / "design-system.css"

TABLE_TAG = re.compile(r"<el-table(?![\w:-])[^>]*>", re.DOTALL)
DIALOG_TAG = re.compile(r"<el-dialog(?![\w:-])[^>]*>", re.DOTALL)
DRAWER_TAG = re.compile(r"<el-drawer(?![\w:-])[^>]*>", re.DOTALL)


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def main() -> int:
    violations: list[str] = []

    css = DESIGN_SYSTEM.read_text(encoding="utf-8") if DESIGN_SYSTEM.exists() else ""
    if ".el-overlay-dialog > .el-dialog" not in css:
        violations.append(
            "frontend/src/styles/design-system.css 缺少弹窗居中规则，"
            "AGENTS.md 要求所有弹窗居中"
        )

    for path in sorted(FRONTEND.rglob("*.vue")):
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT)

        for match in DRAWER_TAG.finditer(text):
            violations.append(
                f"{rel}:{line_of(text, match.start())} 使用了 el-drawer；"
                "表单/详情弹窗必须改为居中 el-dialog（AGENTS.md §1）"
            )

        for match in DIALOG_TAG.finditer(text):
            tag = match.group(0)
            if "append-to-body" not in tag:
                violations.append(
                    f"{rel}:{line_of(text, match.start())} el-dialog 缺少 append-to-body"
                )

        for match in TABLE_TAG.finditer(text):
            tag = match.group(0)
            if "v-col-resize" not in tag:
                violations.append(
                    f"{rel}:{line_of(text, match.start())} el-table 缺少 v-col-resize；"
                    "多列表格必须支持拖拽列宽（AGENTS.md §2）"
                )

    if violations:
        print("界面规范检查未通过：", file=sys.stderr)
        for item in violations:
            print(f"  - {item}", file=sys.stderr)
        return 1

    print("界面规范检查通过：弹窗居中、无抽屉、全部表格支持列宽拖拽")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
