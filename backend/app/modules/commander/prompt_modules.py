"""规划提示词可选模块：让「交付优先 / 成本纪律 / 简洁表达 / 风险预防」可按小院开关。

核心 JSON 契约与人格段不可关闭；可选模块只追加执行风格约束，并按字符预算截断，
避免提示词随人格、记忆、工作流一起膨胀。
"""

from __future__ import annotations

from typing import Any

MODULE_DELIVERY = "delivery"
MODULE_COST = "cost"
MODULE_CONCISE = "concise"
MODULE_RISK = "risk"

PROMPT_MODULES: dict[str, tuple[str, str]] = {
    MODULE_DELIVERY: (
        "交付优先",
        "交付类目标必须产出可下载文件或明确成果；不能只给建议、不能空转。",
    ),
    MODULE_COST: (
        "成本纪律",
        "能一次调用完成的不拆成两次；优先复用成熟流程与已验证助手；"
        "不为了多跑一个助手而拆分无依赖的子任务。",
    ),
    MODULE_CONCISE: (
        "简洁表达",
        "计划标题不超过 20 字；instruction 只写执行必需信息，不写客套话与重复背景。",
    ),
    MODULE_RISK: (
        "风险预防",
        "高风险、不可逆或大额成本动作必须先写明风险与回退路径；" "不自行放大权限，不绕过审批。",
    ),
}

DEFAULT_MODULES = (MODULE_DELIVERY, MODULE_COST, MODULE_CONCISE, MODULE_RISK)
DEFAULT_MAX_CHARS = 4500


def normalize_modules(value: Any) -> list[str]:
    """只保留已注册模块，去重且保持注册顺序。"""
    if not isinstance(value, list | tuple):
        return list(DEFAULT_MODULES)
    enabled = {str(item) for item in value}
    return [code for code in PROMPT_MODULES if code in enabled]


def module_list(enabled: list[str]) -> list[dict[str, Any]]:
    active = set(normalize_modules(enabled))
    return [
        {
            "code": code,
            "title": title,
            "content": content,
            "enabled": code in active,
        }
        for code, (title, content) in PROMPT_MODULES.items()
    ]


def module_block(code: str) -> str:
    item = PROMPT_MODULES.get(code)
    if item is None:
        return ""
    title, content = item
    return f"【{title}】{content}"


def compose(
    core: str,
    persona_text: str,
    enabled: list[str],
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """核心段与人格段必留；可选模块按预算顺序追加，超出即停止。"""
    limit = max(2000, min(int(max_chars or DEFAULT_MAX_CHARS), 8000))
    text = core
    suffix = f"\n{persona_text}" if persona_text else ""
    if len(text) + len(suffix) <= limit:
        text += suffix
    for code in normalize_modules(enabled):
        block = f"\n{module_block(code)}"
        if len(text) + len(block) > limit:
            break
        text += block
    return text


def char_count(text: str) -> int:
    return len(text or "")


def token_estimate(text: str) -> int:
    """粗略估算：中文按 1 字 1 token、英文按 4 字符 1 token 混合取高。"""
    if not text:
        return 0
    return max(1, round(len(text) / 1.6))
