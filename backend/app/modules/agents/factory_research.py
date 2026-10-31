"""Agent 工厂联网情报：用描述里的行业关键词查少量公开资料，供蓝图规划参考。

边界（宁缺毋滥）：
- 只查 1-2 条短查询，整阶段预算 10 秒（两条查询并发）；无网/搜索关闭/源不可用一律降级返回
  ``used=False`` 与原因，绝不阻塞工厂主流程、也不伪报为已联网。
- 情报只用于「增强框架匹配 + 给 LLM 附加参考」，最终仍以本小院知识库与
  现场信息为准；来源标题/链接随蓝图落库，前端可展示可追溯。
- 测试环境默认关闭（conftest 设置 FACTORY_RESEARCH_DISABLED=1），需要覆盖
  时显式删除该变量并注入搜索 Mock。
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.agents import factory_frameworks as ff

log = get_logger(__name__)

#: 整阶段联网预算（秒）：两条查询并发，到点立即收敛，已有结果照用
RESEARCH_BUDGET_SECONDS = 10.0
#: 每条查询取几条结果 / 最多查几条
RESEARCH_COUNT = 4
RESEARCH_MAX_QUERIES = 2
#: 单条查询词上限（搜索引擎对超长中文句子效果差）
QUERY_MAX_CHARS = 48
#: 摘要与来源条数上限（落库体积可控）
SUMMARY_MAX_CHARS = 1200
SOURCES_MAX = 8

_DISABLED_ENV = "FACTORY_RESEARCH_DISABLED"


def _disabled() -> bool:
    return os.environ.get(_DISABLED_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text[:limit].strip()


def build_queries(brief: str, focus: str | None) -> list[str]:
    """从描述提炼短查询：行业标签 + 目标 + 侧重，最多两条。"""
    profile, _ = ff.parse_brief(brief, focus)
    tokens: list[str] = []
    for token in (profile.label, *profile.goals[:3], focus or ""):
        piece = _clip(str(token), 16)
        if piece and piece not in tokens:
            tokens.append(piece)
    queries: list[str] = []
    if tokens:
        queries.append(_clip(" ".join(tokens), QUERY_MAX_CHARS))
    fallback = _clip(brief, QUERY_MAX_CHARS)
    if fallback and fallback not in queries:
        queries.append(fallback)
    return queries[:RESEARCH_MAX_QUERIES]


def _empty(reason: str, queries: list[str] | None = None) -> dict[str, Any]:
    return {
        "used": False,
        "reason": reason,
        "queries": queries or [],
        "provider": "",
        "sources": [],
        "summary": "",
        "fetched_at": None,
    }


async def _collect(
    db: Session, yard_id: str, queries: list[str], deadline: float
) -> tuple[list[dict[str, Any]], str, list[str]]:
    """并发查询多条关键词，按 URL 去重；返回 (来源, 首个成功供应商, 失败原因)。"""
    from app.modules.search.service import search_web

    async def one(query: str) -> tuple[dict[str, Any] | None, str]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None, "联网情报超出时间预算"
        try:
            result = await asyncio.wait_for(
                search_web(db, yard_id, query, count=RESEARCH_COUNT), timeout=remaining
            )
        except TimeoutError:
            return None, "联网情报请求超时"
        except AppError as e:
            return None, e.message
        except Exception as e:  # 情报失败绝不能拖垮工厂
            log.warning("factory research failed (yard=%s): %s", yard_id, e)
            return None, "联网情报失败"
        return result, ""

    results = await asyncio.gather(*(one(query) for query in queries))
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    provider = ""
    failures: list[str] = []
    for result, failure in results:
        if result is None:
            if failure:
                failures.append(failure)
            continue
        provider = provider or str(result.get("provider") or "")
        for hit in result.get("results") or []:
            url = str(hit.get("url") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            sources.append(
                {
                    "title": _clip(str(hit.get("title") or ""), 120),
                    "url": url[:500],
                    "snippet": _clip(str(hit.get("snippet") or ""), 240),
                    "source": _clip(str(hit.get("source") or ""), 60),
                }
            )
            if len(sources) >= SOURCES_MAX:
                break
    return sources, provider, failures


def run_research(db: Session, yard_id: str, brief: str, focus: str | None = None) -> dict[str, Any]:
    """执行联网情报（同步入口，供工厂任务线程调用）；任何失败都降级返回。"""
    queries = build_queries(brief, focus)
    if _disabled():
        return _empty("联网情报已关闭", queries)
    if not queries:
        return _empty("描述过短，无法提炼检索词", queries)
    deadline = time.monotonic() + RESEARCH_BUDGET_SECONDS
    try:
        sources, provider, failures = asyncio.run(_collect(db, yard_id, queries, deadline))
    except Exception as e:
        log.warning("factory research aborted (yard=%s): %s", yard_id, e)
        return _empty("联网情报执行失败", queries)

    if not sources:
        return _empty(failures[0] if failures else "未检索到可用公开资料", queries)
    summary = "\n".join(
        f"- {s['title']}：{s['snippet']}" if s["snippet"] else f"- {s['title']}" for s in sources
    )
    return {
        "used": True,
        "reason": "",
        "queries": queries,
        "provider": provider,
        "sources": sources,
        "summary": summary[:SUMMARY_MAX_CHARS],
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def research_text(research: dict[str, Any]) -> str:
    """把情报压缩成提示词可用的文本（仅标题+摘要）。"""
    if not research.get("used"):
        return ""
    return str(research.get("summary") or "")


__all__ = [
    "RESEARCH_BUDGET_SECONDS",
    "build_queries",
    "research_text",
    "run_research",
]
