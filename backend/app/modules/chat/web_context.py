"""对话前的"时效性问题"联网取数。

背景（2026-11-16 走查）：问"2026 山东山药补贴政策，给官方链接"时，对话
既没有调用 ``web.search``，也没有说明"没搜到"，而是用通用建议 + 自拟网址作答。
根因是问答链路没有把检索能力接到普通会话上（工具只在声明式/指挥官链路可用）。

本模块做**规则优先的确定性补检索**：命中时效性关键词才调用 ``web.search``，
把真实来源塞进上下文，并明确告诉模型"只能引用以下链接、没有就直说没搜到"。
检索失败/无结果时不阻断回答，只注入"未检索到"的事实，避免自拟 URL。
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.logging import get_logger

log = get_logger(__name__)

#: 命中即认为"需要外部事实"的关键词（保守：宁可多搜一次，也不让模型编链接）
_TIMELY_RE = re.compile(
    r"(最新|今年|本年|当前|现在|近期|本月|上月|去年|202[0-9]\s*年?"
    r"|政策|补贴|补助|标准|价格|行情|新闻|公告|通知|发布|官网|官方|链接|网址"
    r"|规定|条例|办法|文件|号文|数据|统计)",
    re.IGNORECASE,
)
#: 纯本地/操作类问题不该触发联网（避免每次记账都搜网）
_LOCAL_ACTION_RE = re.compile(
    r"(记一笔|记一筆|新增|删除|修改|台账|地块|诊断|生成.{0,6}(周报|报表))"
)


def needs_web_search(text: str) -> bool:
    raw = (text or "").strip()
    if not raw or len(raw) < 6:
        return False
    if _LOCAL_ACTION_RE.search(raw) and not re.search(r"(政策|补贴|价格|新闻|公告|官网|链接)", raw):
        return False
    return _TIMELY_RE.search(raw) is not None


def _format_results(data: dict[str, Any]) -> str:
    rows = data.get("results") or []
    if not rows:
        return (
            "【联网检索】本次没有检索到可用结果。回答时必须明确说明“未检索到官方信息”，"
            "不得编造任何网址或文号。"
        )
    lines = ["【联网检索结果】以下是本次真实检索到的来源，只能引用这些链接："]
    for i, row in enumerate(rows[:6], 1):
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or "").strip()
        snippet = str(row.get("snippet") or row.get("content") or "").strip()
        published = str(row.get("published") or "").strip()
        lines.append(f"{i}. {title}｜{url}｜{published}")
        if snippet:
            lines.append(f"   摘要：{snippet[:300]}")
    lines.append("若以上来源不足以回答，请直接说明缺口，不要补充未经检索的链接。")
    return "\n".join(lines)


async def build_web_context(
    db: Session, *, ctx: YardContext, question: str, request_id: str = ""
) -> str:
    """命中时效性问题时返回检索上下文；否则返回空串。"""
    if not needs_web_search(question):
        return ""
    try:
        from app.modules.tools.registry import tool_registry

        data = await tool_registry.execute(
            db,
            "web.search",
            {"query": question[:200], "count": 5},
            ctx,
            request_id or ctx.request_id,
        )
    except Exception as e:  # noqa: BLE001 - 检索失败不能阻断对话主流程
        log.warning("对话前置联网检索失败：%s", e)
        return (
            "【联网检索】检索服务本次不可用。回答时必须说明“未能联网检索”，"
            "不得编造网址、文号或政策条款。"
        )
    return _format_results(data if isinstance(data, dict) else {})
