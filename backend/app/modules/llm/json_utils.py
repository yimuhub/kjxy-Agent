"""LLM 结构化 JSON 输出解析与单行文本清洗公共工具。

消除 farm/agent_service.py、quick_actions/parser.py、devices/analysis_service.py
等模块中重复实现的 Markdown 代码围栏剥离、JSON 字典提取与空白压缩逻辑。
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_llm_json_dict(
    content: str | None, *, error_message: str | None = None
) -> dict[str, Any] | None:
    """从模型输出文本中容错提取 JSON 对象（自动剥离 Markdown 代码围栏与前后缀说明文字）。

    ``error_message`` 传入时，解析失败改为抛 ``ValueError``（调用方可直接捕获并
    落失败原因）；不传则保持历史行为返回 ``None``，兼容既有调用方。
    """
    text = _FENCE_RE.sub("", (content or "").strip()).strip()
    if not text:
        return None
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        sub = text[start : end + 1]
        if sub != text:
            candidates.append(sub)
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    if error_message:
        raise ValueError(error_message)
    return None


def clean_single_line(text: Any, limit: int) -> str:
    """将任意输入合并空白为单行并按字符上限截断。"""
    return " ".join(str(text or "").split())[:limit]
