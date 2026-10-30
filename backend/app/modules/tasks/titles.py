"""任务可读名称（v3.29）：让人一眼看出这条任务在做什么。

标题由「任务类型中文名 + 输入里的主体（问题/需求/工单标题）」拼成，不新增数据库列：
历史任务与新建任务走同一套推导，列表、详情、审计都用同一口径。
"""

from __future__ import annotations

from typing import Any

#: 任务类型 → 中文名；未登记的类型直接展示原始码，避免静默丢信息
TASK_TYPE_LABELS: dict[str, str] = {
    "knowledge_qa": "知识问答",
    "agent_generate": "一句话生成智能体",
    "agent_factory": "智能体工厂",
    "team_run": "班组接力",
    "workorder.dispatch": "工单派发",
    "mock.echo": "回声测试",
}

#: 输入里可作为「主体」的字段，按优先级取第一个非空
_SUBJECT_KEYS = (
    "utterance",
    "question",
    "industry_brief",
    "name",
    "team_name",
    "title",
    "content",
)

_SUBJECT_CHARS = 24


def _clean(text: Any) -> str:
    return " ".join(str(text).split())


def _sources(input_json: dict[str, Any] | None) -> list[dict[str, Any]]:
    """主体来源：先看 data，再看 meta.display（如班组接力把展示信息放 meta）。"""
    if not isinstance(input_json, dict):
        return []
    data = input_json.get("data")
    meta = input_json.get("meta")
    display = meta.get("display") if isinstance(meta, dict) else None
    return [item for item in (data, display) if isinstance(item, dict)]


def task_subject(input_json: dict[str, Any] | None) -> str:
    """从任务输入里取一句主体描述（截断，保留可读性）。"""
    for source in _sources(input_json):
        for key in _SUBJECT_KEYS:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                text = _clean(value)
                return text if len(text) <= _SUBJECT_CHARS else f"{text[:_SUBJECT_CHARS]}…"
    return ""


def task_title(task_type: str, input_json: dict[str, Any] | None) -> str:
    label = TASK_TYPE_LABELS.get(task_type, task_type)
    subject = task_subject(input_json)
    return f"{label}：{subject}" if subject else label
