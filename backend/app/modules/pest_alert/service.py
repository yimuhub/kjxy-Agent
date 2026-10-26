"""虫情预警业务逻辑：无状态纯函数。

默认实现：knowledge.search 检索小院知识库 + Fake LLM 拼装回答，
不调用真实 LLM、不建表、不写 migration，所有查询强制带 yard_id 隔离。

TODO：把 run_pest_alert 内的检索/拼装替换为真实业务逻辑
（输入解析、领域规则、真实 LLM 调用走 app.modules.llm.service.complete_with_logging）。
本文件由脚手架 app.modules.agents.scaffold 自动生成。
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.logging import get_logger
from app.modules.knowledge.service import search as knowledge_search
from app.modules.llm.providers_fake import FakeProvider
from app.modules.llm.schemas import ChatMessage, LLMRequest
from app.modules.pest_alert.schemas import PestAlertInput, PestAlertOutput

log = get_logger(__name__)

#: 脚手架默认依赖的工具；与生成时的 --tools 参数保持一致。
DEFAULT_TOOLS = ["knowledge.search"]


def _retrieve_evidence(
    db: Session, yard_id: str, user_id: str, query: str, top_k: int
) -> tuple[list[dict[str, Any]], list[str]]:
    """小院知识库检索。TODO：按业务替换检索策略 / 数据源。"""
    ctx = YardContext(yard_id=yard_id, user_id=user_id, roles=[], request_id="")
    warnings: list[str] = []
    try:
        hits = knowledge_search(db, ctx, query, top_k=top_k)
    except Exception as e:  # 检索失败不阻断主流程，降级为空证据
        log.warning("pest_alert knowledge.search failed: %s", e)
        warnings.append(f"知识检索失败，已降级为空证据：{e}")
        return [], warnings
    evidence = [
        {
            "title": h.title,
            "source": h.source,
            "quote": h.quote,
            "score": h.score,
            "trust_level": h.trust_level,
        }
        for h in hits
    ]
    return evidence, warnings


def _run_coro_sync(coro):
    """在同步上下文中驱动协程。

    无运行中循环时用 asyncio.run；已在事件循环内（如 pytest asyncio_mode=auto
    的异步测试调用同步 service）时手动步进——FakeProvider.complete 内部无
    await，单步即完成。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    try:
        coro.send(None)
    except StopIteration as stop:
        return stop.value
    coro.close()
    raise RuntimeError("协程中途 yield，无法在运行循环内同步驱动。")


def _fake_llm_answer(question: str, evidence: list[dict[str, Any]]) -> str:
    """Fake LLM 拼装回答。TODO：替换为真实 LLM 调用。"""
    lines = [f"问题：{question}", f"检索到的知识（{len(evidence)} 条）："]
    for item in evidence:
        quote = str(item.get("quote") or "")[:200]
        lines.append(f"- {item.get('title')}：{quote}")
    if not evidence:
        lines.append("（无检索结果）")
    lines.append("请基于以上知识给出简要回答。")
    request = LLMRequest(messages=[ChatMessage(role="user", content="\n".join(lines))])
    response = _run_coro_sync(FakeProvider().complete(request))
    return response.content


def run_pest_alert(
    db: Session, yard_id: str, user_id: str, input_data: dict[str, Any]
) -> dict[str, Any]:
    """虫情预警纯函数入口。TODO：替换为真实业务逻辑。

    参数 input_data 须符合 PestAlertInput；返回 dict 符合 PestAlertOutput。
    """
    parsed = PestAlertInput(**(input_data or {}))
    evidence, warnings = _retrieve_evidence(db, yard_id, user_id, parsed.query, parsed.top_k)
    if not evidence:
        warnings.append("未检索到相关知识，回答为模板拼装，请核验。")
    answer = _fake_llm_answer(parsed.query, evidence)
    return PestAlertOutput(
        answer=answer,
        sources=evidence,
        confidence=0.5 if evidence else 0.0,
        warnings=warnings,
    ).model_dump()
