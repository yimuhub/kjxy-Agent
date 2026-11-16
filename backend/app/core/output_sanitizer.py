"""对外输出清洗：剥离模型复述出来的内部提示结构标记。

背景（2026-11-16 走查）：知识问答/自建助手的回答里直接出现
``<retrieval_results>`` 这类内部边界标记，属于"提示词结构泄漏到用户侧"，
既影响阅读也让用户怀疑答案未处理干净。

原则：只剥离**已知内部标记**，不删除标记包裹的内容（内容可能仍是有用资料）；
幂等、无副作用，可安全用于持久化前与流式输出前。
"""

from __future__ import annotations

import re

#: 内部提示结构标签（成对或自闭合都可能被模型复述）
_INTERNAL_TAGS = (
    "retrieval_results",
    "knowledge_context",
    "tool_result",
    "tool_results",
    "internal_context",
    "system_context",
)

_TAG_RE = re.compile(
    r"</?(?:" + "|".join(_INTERNAL_TAGS) + r")\b[^>]*>", re.IGNORECASE
)
#: 模型偶尔会写"以上 <retrieval_results> 标签内…"这类说明句，整行去掉
_INSTRUCTION_LINE_RE = re.compile(
    r"(?m)^\s*以上\s*<(?:"
    + "|".join(_INTERNAL_TAGS)
    + r")>.*?$",
    re.IGNORECASE,
)
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


def strip_internal_markup(text: str | None) -> str:
    """剥离内部结构标签与相关说明行，保留正文与引用线索。"""
    if not text:
        return ""
    cleaned = _INSTRUCTION_LINE_RE.sub("", text)
    cleaned = _TAG_RE.sub("", cleaned)
    # 标签被剥离后可能留下空行堆叠，压缩为最多一个空行
    cleaned = _MULTI_BLANK_RE.sub("\n\n", cleaned)
    return cleaned.strip()
