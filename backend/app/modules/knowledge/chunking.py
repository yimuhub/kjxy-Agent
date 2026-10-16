"""文本切块：按段落贪心拼接，超长段落硬切；保留原文顺序。"""

import re

from app.core.config import settings


def _hard_cut(text: str, chunk_size: int, overlap: int) -> list[str]:
    """超长段落硬切，步长 = chunk_size - overlap，保证相邻片段有重叠。"""
    pieces: list[str] = []
    step = max(1, chunk_size - overlap)
    start = 0
    while start < len(text):
        pieces.append(text[start : start + chunk_size])
        if start + chunk_size >= len(text):
            break
        start += step
    return pieces


def split_text(
    text: str,
    chunk_size: int = settings.chunk_size,
    overlap: int = settings.chunk_overlap,
) -> list[str]:
    """把文本切成有序块列表。

    - 按空行分段，贪心拼接使每块尽量接近但不超过 chunk_size；
    - 单个段落超过 chunk_size 时硬切；
    - 除首块外，每块头部附带上一块尾部的 overlap 个字符作为重叠窗口。
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须为正数")
    overlap = max(0, min(overlap, chunk_size - 1))

    text = (text or "").strip()
    if not text:
        return []

    # 1) 段落 → 片段（超长段落硬切）
    segments: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= chunk_size:
            segments.append(para)
        else:
            segments.extend(_hard_cut(para, chunk_size, overlap))

    # 2) 贪心拼接，保持顺序
    base: list[str] = []
    current = ""
    for seg in segments:
        candidate = f"{current}\n\n{seg}" if current else seg
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            if current:
                base.append(current)
            current = seg
    if current:
        base.append(current)

    # 3) 重叠窗口：每块（除首块）头部拼接上一块的尾部
    if overlap <= 0 or len(base) <= 1:
        return base
    result = [base[0]]
    for prev, cur in zip(base, base[1:], strict=False):
        result.append(f"{prev[-overlap:]}\n\n{cur}")
    return result
