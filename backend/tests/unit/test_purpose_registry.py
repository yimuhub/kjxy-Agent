"""能力用途码登记护栏。

``app/modules/llm/capabilities.py`` 的 CAPABILITIES 是**代码写死的事实**：
每个 ``LLMMetadata(purpose=...)`` 写入点都必须在其中登记，否则「模型中心 →
能力绑定」页签会漏掉一条真实链路（调用发生了，但页签里没有这条用途，
运营无法为它单独绑定模型/核算成本）。

本护栏用 AST 扫描全部 ``purpose="xxx"`` 字面量写入点，断言：
1. 每个写入点用的用途码都已在 CAPABILITIES 中登记；
2. 登记的用途码没有"清单里有、代码里从不写"的死条目。

这正是审查中发现"5 个 purpose 未登记"的那类错位，用测试把它钉死。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from app.modules.llm.capabilities import CAPABILITIES, CAPABILITY_CODES

APP_DIR = Path(__file__).resolve().parents[2] / "app"


def _iter_purpose_literals() -> set[str]:
    """扫描 app/ 下所有 ``purpose="xxx"`` 写入点（含模块级常量间接引用）。

    用途码有两种写法：直接字面量 ``purpose="chat_plan"``，以及模块级常量
    ``GENERATE_PURPOSE = "tool_generation"`` 后 ``purpose=GENERATE_PURPOSE``。
    两种都要认，否则常量写法会被误判成"登记了但代码里从不写"的死条目。
    """
    found: set[str] = set()
    for path in APP_DIR.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - 忽略非源码文件
            continue
        # 本文件的模块级字符串常量：名字 -> 值
        constants: dict[str, str] = {}
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                constants[node.targets[0].id] = node.value.value

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "purpose":
                    continue
                value = keyword.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    found.add(value.value)
                elif isinstance(value, ast.Name) and value.id in constants:
                    found.add(constants[value.id])
    return found


WRITTEN_PURPOSES = _iter_purpose_literals()


@pytest.mark.parametrize("purpose", sorted(WRITTEN_PURPOSES))
def test_written_purpose_is_registered(purpose: str) -> None:
    """每个真实写入的用途码都必须登记在 CAPABILITIES 里。"""
    assert purpose in CAPABILITY_CODES, (
        f"用途码 {purpose!r} 被写进模型调用，但没在 app/modules/llm/capabilities.py "
        f"的 CAPABILITIES 里登记 —— 「模型中心 → 能力绑定」页签会漏掉这条真实链路。"
        f"请补一条 Capability(code={purpose!r}, ...)。"
    )


def test_registered_capability_has_no_dead_entry() -> None:
    """登记的用途码不能是死条目：代码里必须真的在写它。"""
    # 平台兜底码允许没有写入点（未标注用途的调用统一归集到它）
    dead = [code for code in CAPABILITY_CODES if code not in WRITTEN_PURPOSES]
    assert not dead, (
        f"这些用途码登记在 CAPABILITIES 里但代码中从不写入：{dead} —— "
        f"「模型中心」会显示一条不存在的链路，请删除或补上真实写入点。"
    )


def test_capability_codes_are_unique() -> None:
    codes = [item.code for item in CAPABILITIES]
    assert len(codes) == len(set(codes)), "CAPABILITIES 里存在重复的用途码"


def test_capability_labels_are_present() -> None:
    for item in CAPABILITIES:
        assert item.label.strip(), f"用途码 {item.code} 缺少中文名"
        assert item.group.strip(), f"用途码 {item.code} 缺少分组"
        assert item.hint.strip(), f"用途码 {item.code} 缺少用途说明"
