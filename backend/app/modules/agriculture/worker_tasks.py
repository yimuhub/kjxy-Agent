"""问答异步任务。

- 经 app.modules.tasks.handlers.register_handler 注册 "knowledge_qa" 处理器，
  Celery worker（tasks.execute）按 task_type 调度执行；成功时返回 QAOutput 字典
  （由 tasks worker 写入 task.result_json），失败抛 AppError 由 worker 记为 failed。
- 同时提供 @celery_app.task(name="qa.answer") 直连入口（备用）。
- tasks/celery 缺失时仅保留同步可调用的 run_knowledge_qa，不影响 import。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger(__name__)

QA_TASK_TYPE = "knowledge_qa"


async def run_knowledge_qa(
    input_data: dict,
    context_dict: dict,
    db: Any = None,
) -> dict[str, Any]:
    """执行 agriculture_qa agent，返回 {"status","output","citations","warnings"}。"""
    from app.modules.agriculture.qa_agent import AgricultureQAAgent, QAAgentContext

    agent_ctx = QAAgentContext(
        tenant_id=str(context_dict.get("tenant_id", "")),
        user_id=str(context_dict.get("user_id", "") or context_dict.get("actor_id", "")),
        roles=list(context_dict.get("roles", []) or []),
        request_id=str(context_dict.get("request_id", "req_unknown")),
        db=db,
    )
    agent = AgricultureQAAgent()
    result = await agent.run(dict(input_data or {}), agent_ctx)
    output = result.output if isinstance(result.output, dict) else {}
    return {
        "status": result.status,
        "output": output,
        "citations": list(getattr(result, "citations", []) or []),
        "warnings": list(getattr(result, "warnings", []) or []),
        "error": getattr(result, "error", None),
    }


def knowledge_qa_handler(input_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """tasks 处理器：Handler 签名 (input_data, context) -> dict。

    context 来自 tasks worker：{"task_id","tenant_id","actor_id","request_id"}。
    db 会话由 worker_tasks 的 _get_session_factory 提供，保证知识检索走请求级会话。
    返回 QAOutput 字典，worker 负责写入 task.result_json。
    """
    from app.modules.tasks.worker_tasks import _get_session_factory

    db = _get_session_factory()()
    try:
        result = asyncio.run(run_knowledge_qa(input_data, context, db))
    finally:
        db.close()
    if result["status"] != "succeeded":
        detail = result.get("error") or "; ".join(result.get("warnings") or []) or "未知错误"
        raise AppError(
            "QA_FAILED",
            f"问答执行失败：{detail}",
            http_status=502,
        )
    return result["output"]


# 注册到 tasks 全局处理器表（tasks 模块约定）
try:
    from app.modules.tasks.handlers import register_handler

    register_handler(QA_TASK_TYPE)(knowledge_qa_handler)
    log.info("task handler registered: %s", QA_TASK_TYPE)
except ImportError as e:
    log.warning("tasks.handlers unavailable, knowledge_qa handler not registered: %s", e)

# 兼容旧用法的本地表
TASK_HANDLERS: dict[str, Any] = {QA_TASK_TYPE: knowledge_qa_handler}


def _load_celery_app() -> Any | None:
    for path in ("app.workers.celery_app", "app.core.celery_app", "app.worker"):
        try:
            module = __import__(path, fromlist=["celery_app"])
            app = getattr(module, "celery_app", None)
            if app is not None:
                return app
        except ImportError:
            continue
    return None


_celery_app = _load_celery_app()

if _celery_app is not None:

    @_celery_app.task(name="qa.answer", bind=True)
    def qa_answer_task(self, task_id: str, input_data: dict, context_dict: dict):  # type: ignore[no-redef]
        """Celery 直连任务入口（备用；主路径为 tasks.execute → knowledge_qa handler）。"""
        result = asyncio.run(run_knowledge_qa(input_data, context_dict))
        return result

else:
    qa_answer_task = None  # type: ignore[assignment]
    log.info("celery_app not available; qa.answer not registered as celery task")
