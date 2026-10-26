"""农业问答路由。

POST /qa/answers：mode=sync 直接运行 agent；mode=async 经 tasks 服务建任务
（TaskService.create + enqueue_task 投递 Celery tasks.execute）。
tasks 模块缺失时 async 自动回退为同步执行（warnings 中注明）。
模块 import 时向本模块 agent 注册表注册 agriculture_qa。
"""

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.core.deps import YardContext, get_db_session, get_yard_context
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.responses import ok
from app.modules.agriculture.qa_agent import (
    AgricultureQAAgent,
    QAAgentContext,
    get_global_registry,
)
from app.modules.agriculture.schemas import QAInput
from app.modules.audit.service import log_audit
from app.modules.knowledge.chunking import estimate_token_count
from app.modules.llm.models import LLMTrace

log = get_logger(__name__)
router = APIRouter(prefix="/qa", tags=["agriculture-qa"])

TaskService: Any
enqueue_task: Any
try:
    from app.modules.tasks.service import TaskService
    from app.modules.tasks.worker_tasks import enqueue_task
except ImportError:  # pragma: no cover - 降级分支
    TaskService = None
    enqueue_task = None


def _register() -> None:
    try:
        get_global_registry().register(AgricultureQAAgent.code, AgricultureQAAgent())
    except Exception as e:
        log.warning("agriculture_qa registration skipped: %s", e)


_register()

_ASYNC_DEGRADED_WARNING = "异步任务队列暂不可用，已降级为同步执行。"


@router.post("/answers")
async def answer_question(
    body: QAInput,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db=Depends(get_db_session),
):
    agent_ctx = QAAgentContext(
        yard_id=ctx.yard_id,
        user_id=ctx.user_id,
        roles=list(ctx.roles),
        request_id=ctx.request_id,
        db=db,
    )
    input_data = {"question": body.question, "top_k": body.top_k, "crop_type": body.crop_type}

    if body.mode == "async":
        if TaskService is not None:
            try:
                task = TaskService.create(
                    db,
                    ctx,
                    task_type="knowledge_qa",
                    input=input_data,
                    request_id=ctx.request_id,
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
            enqueued = enqueue_task(task.id) if enqueue_task is not None else False
            log_audit(
                db,
                yard_id=ctx.yard_id,
                actor_id=ctx.user_id,
                action="qa.answer.enqueue",
                resource_type="task",
                resource_id=task.id,
                result="success",
                request_id=ctx.request_id,
                metadata={"enqueued": enqueued},
            )
            db.commit()
            return ok({"task_id": task.id, "mode": "async", "enqueued": enqueued}, request)

    agent = AgricultureQAAgent()
    result = await agent.run(input_data, agent_ctx)
    if result.status != "succeeded":
        raise AppError("QA_FAILED", "问答执行失败，请稍后重试。", http_status=502)

    output = dict(result.output)
    if body.mode == "async" and TaskService is None:
        warnings = list(output.get("warnings", []))
        if _ASYNC_DEGRADED_WARNING not in warnings:
            warnings.append(_ASYNC_DEGRADED_WARNING)
        output["warnings"] = warnings

    # 补充本次问答的 Token 统计（供前端问答卡片展示）
    latest_trace = (
        db.query(LLMTrace)
        .filter(LLMTrace.yard_id == ctx.yard_id)
        .order_by(LLMTrace.created_at.desc())
        .first()
    )
    citations_list = output.get("citations") or []
    rag_tokens = sum(estimate_token_count(str(c.get("quote") or "")) for c in citations_list)
    if latest_trace and latest_trace.input_tokens is not None:
        in_tok = int(latest_trace.input_tokens or 0)
        out_tok = int(latest_trace.output_tokens or 0)
        output["token_usage"] = {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
            "rag_tokens": rag_tokens,
            "model": latest_trace.model,
            "latency_ms": latest_trace.latency_ms,
        }
    else:
        in_tok = estimate_token_count(body.question) + rag_tokens
        out_tok = estimate_token_count(str(output.get("answer") or ""))
        output["token_usage"] = {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
            "rag_tokens": rag_tokens,
            "model": "qa",
            "estimated": True,
        }

    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="qa.answer",
        resource_type="qa",
        resource_id=None,
        result="success",
        request_id=ctx.request_id,
        metadata={"mode": body.mode, "needs_expert_review": output.get("needs_expert_review")},
    )
    db.commit()
    return ok(output, request)
