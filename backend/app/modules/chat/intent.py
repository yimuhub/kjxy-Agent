"""对话意图识别：区分"提问"与"下达指令"。

口径（保守优先，宁可漏判不可误判）：
- create 类意图必须**同时**命中"创建动词"与"能力名词"；
- 出现疑问词（吗/呢/怎么/为什么/…）时降级为提问——"怎么做个助手"是咨询，
  不是指令，不能据此建能力；
- install 类意图命中"装载动词 + 能力名词"（走市场安装，不建草稿）；
- 未命中一律是提问 → 走既有问答链路（**零回归**）。

纯规则实现：不调模型、不写库、无外部依赖，判定结果可复现。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 创建动词（命中其一即视为"下达指令"的可能）
CREATE_VERBS: tuple[str, ...] = (
    "生成",
    "创建",
    "新建",
    "做一个",
    "做个",
    "建一个",
    "建个",
    "搭一个",
    "搭个",
    "设计一个",
    "设计个",
    "写一个",
    "写个",
    "制作",
    "开发",
    "来一个",
)

#: 能力名词（命中其一才算"要造的东西"）
CAPABILITY_NOUNS: tuple[str, ...] = (
    "助手",
    "智能体",
    "agent",
    "能力",
    "工具",
    "技能",
    "班组",
    "专家",
    "机器人",
)

#: 装载动词（走市场安装）
INSTALL_VERBS: tuple[str, ...] = ("安装", "装上", "装载", "引入", "接入")

#: 疑问词（命中即降级为提问）
QUESTION_WORDS: tuple[str, ...] = (
    "吗",
    "呢",
    "怎么",
    "如何",
    "为什么",
    "多少",
    "是否",
    "能不能",
    "可不可以",
    "请问",
    "是什么",
    "哪些",
    "有没有",
)

#: 意图类型
INTENT_QA = "qa"
INTENT_CREATE = "create"
INTENT_INSTALL = "install"

_CODE_SAFE = re.compile(r"[^a-z0-9_]+")


@dataclass(frozen=True)
class Intent:
    """意图判定结果。"""

    kind: str
    target: str
    reason: str

    @property
    def is_action(self) -> bool:
        return self.kind in (INTENT_CREATE, INTENT_INSTALL)


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def detect(text: str) -> Intent:
    """判定一条用户输入的意图。"""
    raw = (text or "").strip()
    if not raw:
        return Intent(INTENT_QA, "agent", "空输入，按提问处理")
    lowered = raw.lower()
    noun_hit = _has(lowered, CAPABILITY_NOUNS)
    if not noun_hit:
        return Intent(INTENT_QA, "agent", "未命中能力名词，按提问处理")
    if _has(raw, QUESTION_WORDS):
        return Intent(INTENT_QA, "agent", "含疑问词，按提问处理")
    if _has(raw, INSTALL_VERBS):
        return Intent(INTENT_INSTALL, "market", "命中装载动词，走市场安装")
    if _has(raw, CREATE_VERBS):
        target = "team" if "班组" in lowered else "agent"
        return Intent(INTENT_CREATE, target, "命中创建动词与能力名词，按生成能力处理")
    return Intent(INTENT_QA, "agent", "仅命中能力名词，按提问处理")


__all__ = [
    "CAPABILITY_NOUNS",
    "CREATE_VERBS",
    "INSTALL_VERBS",
    "INTENT_CREATE",
    "INTENT_INSTALL",
    "INTENT_QA",
    "Intent",
    "QUESTION_WORDS",
    "detect",
]
