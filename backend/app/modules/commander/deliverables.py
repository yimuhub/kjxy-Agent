"""文件交付物识别：把「做个 PPT / Word / Excel / PDF」落成真实可下载文件。

只在文本**显式点名文件格式**时返回格式；不像 ``docs_service.format_from_text``
那样默认 Word——指挥官提示词里混着大量非交付文本，默认值会把普通问答误判成要文件。
"""

from __future__ import annotations

import re
from typing import Any

#: 文档生成工具码（声明式 Agent 与指挥官自执行器共用）
DELIVERABLE_TOOL = "doc.generate"

FORMAT_LABELS: dict[str, str] = {
    "pptx": "PPT 演示文稿",
    "docx": "Word 文档",
    "xlsx": "Excel 表格",
    "pdf": "PDF 文档",
    "md": "Markdown 文档",
}

FORMAT_EXT: dict[str, str] = {
    "pptx": ".pptx",
    "docx": ".docx",
    "xlsx": ".xlsx",
    "pdf": ".pdf",
    "md": ".md",
}

#: 子任务标题用的短名（带空格的长名只用于正文说明）
SHORT_LABELS: dict[str, str] = {
    "pptx": "PPT",
    "docx": "Word",
    "xlsx": "Excel",
    "pdf": "PDF",
    "md": "Markdown",
}

_FORMAT_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pptx", ("pptx", "ppt", "幻灯片", "演示文稿", "powerpoint")),
    ("xlsx", ("xlsx", "excel", "电子表格", "表格文件")),
    ("docx", ("docx", "word")),
    ("pdf", ("pdf",)),
    ("md", ("markdown",)),
)

#: 出现这些词才算「要产出文件」；只提到格式（如「输出 PPT 大纲」）不算
_FILE_ACTIONS: tuple[str, ...] = (
    "导出",
    "下载",
    "保存",
    "交付",
    "文件",
    "附件",
    "发我",
    "给我做",
    "帮我做",
    "做一份",
    "做一个",
    "做一张",
    "写一份",
    "写一个",
    "整一份",
    "弄一份",
    "出一份",
    "产出",
)

#: 格式词紧邻这些结构名词时，是在要内容（大纲/文案），不是要文件
_STRUCTURE_WORDS: tuple[str, ...] = (
    "大纲",
    "提纲",
    "要点",
    "文案",
    "内容",
    "结构",
    "素材",
    "资料",
    "脚本",
    "演讲稿",
    "思路",
)


#: 结构名词前的「写作动词」：写明「撰写 PPT 文案」时即使验收提到文件也算内容任务
_WRITING_VERBS: tuple[str, ...] = ("撰写", "编写", "拟写", "起稿", "润色", "改写")


def _first_format_pos(text: str) -> int | None:
    lowered = (text or "").lower()
    positions = [
        lowered.find(word)
        for _, words in _FORMAT_WORDS
        for word in words
        if lowered.find(word) >= 0
    ]
    return min(positions) if positions else None


def label(fmt: str) -> str:
    return FORMAT_LABELS.get(fmt, fmt)


#: 生成需求时去掉「交付机制」话术，避免文档标题被拼进「.pptx 文件」之类的尾巴
_MECHANICS_RE = re.compile(
    r"(?:并|然后)?(?:调用\s*doc\.generate[^。\n]*|"
    r"生成可下载的?[^。\n]*\.(?:pptx|docx|xlsx|pdf|md)[^。\n]*|"
    r"作为会话附件交付[^。\n]*)",
    re.I,
)


def clean_requirement(text: str) -> str:
    """去掉交付机制描述，只保留内容需求（标题/素材来源）。"""
    cleaned = _MECHANICS_RE.sub("", text or "")
    return re.sub(r"\s{2,}", " ", cleaned).strip(" ，,。;；\n")


def detect(text: str) -> str | None:
    """识别要交付的文件格式；多个格式词同时出现时取**最后出现**的那个。"""
    lowered = (text or "").lower()
    best: tuple[int, str] | None = None
    for fmt, words in _FORMAT_WORDS:
        for word in words:
            pos = lowered.rfind(word)
            if pos >= 0 and (best is None or pos > best[0]):
                best = (pos, fmt)
    return best[1] if best else None


def subtask_format(subtask: dict[str, Any] | Any) -> str | None:
    """子任务是否要求产出文件；是则返回格式。

    只有「显式格式词 + 文件动作」或「明确写了 doc.generate」才判定为交付任务，
    避免把「整理 PPT 要点」这类研究任务也拉到文档渲染。
    """

    def _field(name: str) -> str:
        value = subtask.get(name) if isinstance(subtask, dict) else getattr(subtask, name, "")
        return str(value or "")

    text = " ".join(_field(key) for key in ("title", "instruction", "expected_output")).strip()
    fmt = detect(text)
    if not fmt:
        return None
    if DELIVERABLE_TOOL in text:
        return fmt
    # 「撰写/编写 PPT 文案」：格式词前是写作动词 → 要的是内容，不是文件
    pos = _first_format_pos(text)
    if pos is not None and any(verb in text[:pos] for verb in _WRITING_VERBS):
        return None
    if any(action in text for action in _FILE_ACTIONS):
        return fmt
    # 「生成/制作 + 格式」也视为交付，除非明确是在要大纲/文案
    if ("生成" in text or "制作" in text) and not any(word in text for word in _STRUCTURE_WORDS):
        return fmt
    return None


def build_task(requirement: str, fmt: str, *, seq: int, depends_on: list[int]) -> dict[str, Any]:
    """规划兜底/补齐用的交付子任务：指令里明确要求生成并返回可下载文件。"""
    need = (requirement or "").strip()[:1500] or "按用户需求生成"
    ext = FORMAT_EXT.get(fmt, f".{fmt}")
    return {
        "seq": seq,
        "title": f"生成{SHORT_LABELS.get(fmt, fmt.upper())}交付物",
        "instruction": (
            f"调用 {DELIVERABLE_TOOL} 工具生成可下载的{label(fmt)}（{ext}），"
            "并作为会话附件交付给用户。\n"
            f"主题与要求：{need}\n"
            "内容要求：结论先行、结构完整（标题页/核心要点/数据与结论/下一步建议）；"
            "数据缺失处标注「待补充」，不得编造数字。"
        ),
        "expected_output": f"{label(fmt)}文件（{ext}），可在会话中预览与下载",
        "agent_hint": "文档生成",
        "agent_code": "",
        "depends_on": list(depends_on),
    }


def _merge_into_last(subtasks: list[dict[str, Any]], requirement: str, fmt: str) -> None:
    """已达子任务上限：把文件要求并入最后一个任务，保证仍有交付物。"""
    ext = FORMAT_EXT.get(fmt, f".{fmt}")
    tail = subtasks[-1]
    requirement_text = (requirement or "").strip()[:1500]
    tail["instruction"] = (
        f"{str(tail.get('instruction') or '').strip()}\n\n"
        f"完成后必须调用 {DELIVERABLE_TOOL} 生成可下载的{label(fmt)}（{ext}）"
        "并作为会话附件交付。" + (f"\n交付主题：{requirement_text}" if requirement_text else "")
    )[:8000]
    tail["expected_output"] = f"{label(fmt)}文件（{ext}），可在会话中预览与下载"


def ensure_plan_deliverable(
    plan: dict[str, Any],
    requirement: str,
    fmt: str,
    *,
    max_subtasks: int,
) -> bool:
    """确保计划里有一个交付子任务；返回是否新增/改写了子任务。

    已有子任务明确要求该格式文件时只登记 ``plan["deliverable"]``；否则追加一个
    依赖全部前序任务的交付任务（达到上限时并入最后一个任务）。
    """
    plan["deliverable"] = {"format": fmt, "label": label(fmt)}
    subtasks = [item for item in plan.get("subtasks") or [] if isinstance(item, dict)]
    if not subtasks:
        return False
    if any(subtask_format(item) == fmt for item in subtasks):
        return False
    if len(subtasks) < max_subtasks:
        seq = len(subtasks) + 1
        deps = [int(item["seq"]) for item in subtasks]
        subtasks.append(build_task(requirement, fmt, seq=seq, depends_on=deps))
    else:
        _merge_into_last(subtasks, requirement, fmt)
    plan["subtasks"] = subtasks
    criteria = plan.get("success_criteria")
    if isinstance(criteria, list):
        note = f"交付可下载的{label(fmt)}（{FORMAT_EXT.get(fmt, '.' + fmt)}）"
        if note not in criteria:
            criteria.append(note)
    return True


def compact_for_delivery(plan: dict[str, Any], fmt: str, *, max_content: int = 1) -> bool:
    """单文件交付：把多个前置内容任务并成一个，避免预算被前置步骤吃光。

    规划模型容易把一个「做 30 页 PPT」拆成「大纲 / 素材 / 视觉规范 / 图表规格 /
    内容填充 / 交付」六步，前五步各调一次模型，很容易在交付前把 token 预算耗尽，
    最终一份文件都出不来。这里在**只有一个交付任务**时收敛：
    前置任务合并为「整理交付内容与素材」，交付任务依赖它。

    返回是否发生了合并。
    """
    subtasks = [item for item in plan.get("subtasks") or [] if isinstance(item, dict)]
    delivery = [item for item in subtasks if subtask_format(item) == fmt]
    if not delivery:
        return False
    # 规划模型常把「设计版式」「终检交付」都写成含 .pptx 的任务，只认最后一个当交付，
    # 其余按内容任务处理（否则同一份文件会被渲染三遍，白白烧预算）
    deliver = max(delivery, key=lambda item: int(item.get("seq") or 0))
    contents = [item for item in subtasks if item is not deliver]
    if len(contents) <= max_content:
        return False
    lines = [
        f"- {str(item.get('title') or '').strip()}：{str(item.get('instruction') or '').strip()}"
        for item in contents
    ]
    merged = {
        "seq": 1,
        "title": "整理交付内容与素材",
        "instruction": (
            f"为最终{label(fmt)}一次性准备好内容与素材，按下列要求合并完成：\n"
            + "\n".join(lines)[:3000]
            + "\n输出结构化内容（章节/页级要点/可用数据/视觉与图表建议），"
            "缺数据标注「待补充」，不要生成文件。"
        ),
        "expected_output": "结构化内容与素材清单",
        "agent_hint": "内容整理",
        "agent_code": "",
        "depends_on": [],
    }
    compacted = {**deliver, "seq": 2, "depends_on": [1]}
    plan["subtasks"] = [merged, compacted]
    return True


__all__ = [
    "DELIVERABLE_TOOL",
    "FORMAT_EXT",
    "FORMAT_LABELS",
    "SHORT_LABELS",
    "build_task",
    "clean_requirement",
    "compact_for_delivery",
    "detect",
    "ensure_plan_deliverable",
    "label",
    "subtask_format",
]
