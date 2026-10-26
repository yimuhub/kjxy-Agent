"""对话意图识别：区分"提问"与"下达指令"。

口径（保守优先，宁可漏判不可误判）：
- create 类意图必须**同时**命中"创建动词"与"能力名词"；
- 出现疑问词（吗/呢/怎么/为什么/…）时降级为提问——"怎么做个助手"是咨询，
  不是指令，不能据此建能力；
- install 类意图命中"装载动词 + 能力名词"（走市场安装，不建草稿）；
- doc 类意图命中"产出动词 + 文档名词"（Word/Excel/PPT/PDF/Markdown/报告/方案…），
  走文档生成；疑问词同样降级，能力名词优先级更高（"生成一个助手"仍是造能力）；
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

#: 文档名词（命中其一才算"要产出的文档"；格式词本身也算）
DOC_NOUNS: tuple[str, ...] = (
    "文档",
    "报告",
    "周报",
    "日报",
    "月报",
    "年报",
    "方案",
    "表格",
    "台账",
    "清单",
    "合同",
    "纪要",
    "汇报",
    "说明书",
    "总结",
    "演示文稿",
    "幻灯片",
    "ppt",
    "excel",
    "word",
    "pdf",
    "markdown",
    "md文件",
)

#: 产出动词（与文档名词同时命中才判为文档生成；"写个周报"也算）
DOC_VERBS: tuple[str, ...] = (
    *CREATE_VERBS,
    "写一份",
    "写份",
    "写一篇",
    "写篇",
    "起草",
    "撰写",
    "整理成",
    "汇总成",
    "导出",
    "输出",
    "形成",
    "列出",
)

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
INTENT_DOC = "doc"

_CODE_SAFE = re.compile(r"[^a-z0-9_]+")


@dataclass(frozen=True)
class Intent:
    """意图判定结果。"""

    kind: str
    target: str
    reason: str

    @property
    def is_action(self) -> bool:
        return self.kind in (INTENT_CREATE, INTENT_INSTALL, INTENT_DOC)


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def detect(text: str) -> Intent:
    """判定一条用户输入的意图。"""
    raw = (text or "").strip()
    if not raw:
        return Intent(INTENT_QA, "agent", "空输入，按提问处理")
    lowered = raw.lower()
    # 能力名词优先：命中就走能力链路（含"生成一个助手文档"这类混合句）
    if _has(lowered, CAPABILITY_NOUNS):
        if _has(raw, QUESTION_WORDS):
            return Intent(INTENT_QA, "agent", "含疑问词，按提问处理")
        if _has(raw, INSTALL_VERBS):
            return Intent(INTENT_INSTALL, "market", "命中装载动词，走市场安装")
        if _has(raw, CREATE_VERBS):
            target = "team" if "班组" in lowered else "agent"
            return Intent(INTENT_CREATE, target, "命中创建动词与能力名词，按生成能力处理")
        return Intent(INTENT_QA, "agent", "仅命中能力名词，按提问处理")
    # 文档链路：产出动词 + 文档名词；疑问句仍按提问（"这份报告怎么改"）
    if _has(lowered, DOC_NOUNS) and _has(raw, DOC_VERBS) and not _has(raw, QUESTION_WORDS):
        return Intent(INTENT_DOC, "document", "命中产出动词与文档名词，按生成文档处理")
    return Intent(INTENT_QA, "agent", "未命中动作意图，按提问处理")


__all__ = [
    "CAPABILITY_NOUNS",
    "CREATE_VERBS",
    "DOC_NOUNS",
    "DOC_VERBS",
    "INSTALL_VERBS",
    "INTENT_CREATE",
    "INTENT_DOC",
    "INTENT_INSTALL",
    "INTENT_QA",
    "Intent",
    "QUESTION_WORDS",
    "detect",
]
