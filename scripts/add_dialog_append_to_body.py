#!/usr/bin/env python3
"""给所有 <el-dialog> 补 append-to-body。

背景：Element Plus 的 el-dialog 默认把遮罩层就地渲染在组件所在位置；本项目的
.el-card / .glass-panel 带 backdrop-filter，会成为 position:fixed 的包含块，
于是遮罩层被卡片盒子裁掉、弹窗跑到卡片里居中（v3.25.1 修复「弹窗被外框框住」）。

用法：python3 scripts/add_dialog_append_to_body.py [--check]
  --check 只报告缺哪些文件，不写盘（供 CI 门禁用）
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"

OPEN_TAG = re.compile(r"<el-dialog(?=[\s/>])")
# v-model / v-bind 属 TWO_WAY_BINDING / OTHER_DIRECTIVES 组，必须排在普通属性之前
ANCHOR_PRIMARY = ("v-bind=", "v-model=")
ANCHOR_SECONDARY = ("v-if=", "v-else-if=", "v-else=", "v-show=", "v-cloak=")
EVENT_PREFIX = ("@", "v-on:")


def tag_end(text: str, start: int) -> int:
    """返回开标签 '>' 的下标（跳过引号里的 >）。"""
    i = start
    quote = ""
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == ">":
            return i
        i += 1
    raise ValueError("未找到标签结束符")


def patch_file(path: Path, check: bool) -> int:
    src = path.read_text(encoding="utf-8")
    out = []
    pos = 0
    changed = 0
    while True:
        m = OPEN_TAG.search(src, pos)
        if not m:
            out.append(src[pos:])
            break
        out.append(src[pos:m.start()])
        end = tag_end(src, m.start())
        tag = src[m.start():end + 1]
        pos = end + 1
        if "append-to-body" in tag or "append-to=" in tag:
            out.append(tag)
            continue
        new_tag = insert_attr(tag)
        changed += 1
        out.append(tag if check else new_tag)
    if not check and changed:
        path.write_text("".join(out), encoding="utf-8")
    return changed


def insert_attr(tag: str) -> str:
    """在保持 vue/attributes-order 顺序的前提下插入 append-to-body。"""
    lines = tag.split("\n")
    if len(lines) == 1:
        # 单行写法：插到第一个事件属性之前，没有事件就插到 '>' 之前
        body = lines[0]
        cut = len(body) - 1  # '>' 的位置
        for idx, ch in enumerate(body):
            if ch in "@" and body[idx - 1] in " \t\n":
                cut = idx
                break
            if body.startswith("v-on:", idx):
                cut = idx
                break
        return body[:cut].rstrip() + " append-to-body" + body[cut:]
    # 多行写法：锚点之后插入一行
    anchor = -1
    for key in ANCHOR_PRIMARY:
        for idx, line in enumerate(lines):
            if line.strip().startswith(key):
                anchor = max(anchor, idx)
    if anchor < 0:
        for key in ANCHOR_SECONDARY:
            for idx, line in enumerate(lines):
                if line.strip().startswith(key):
                    anchor = max(anchor, idx)
    if anchor < 0:
        anchor = 0
    indent = re.match(r"[ \t]*", lines[anchor]).group(0)
    lines.insert(anchor + 1, f"{indent}append-to-body")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查不修改")
    args = ap.parse_args()

    missing = []
    total = 0
    for path in sorted(SRC.rglob("*.vue")):
        n = patch_file(path, args.check)
        if n:
            missing.append((path.relative_to(ROOT).as_posix(), n))
            total += n
    if args.check:
        if missing:
            print("以下弹窗缺少 append-to-body：")
            for name, n in missing:
                print(f"  {name} ({n})")
            return 1
        print("所有 el-dialog 均已设置 append-to-body")
        return 0
    for name, n in missing:
        print(f"已补 append-to-body：{name} ({n})")
    print(f"共处理 {total} 处弹窗")
    return 0


if __name__ == "__main__":
    sys.exit(main())
