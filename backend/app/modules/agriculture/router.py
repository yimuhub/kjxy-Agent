"""农业问答路由。

POST /qa/answers：mode=sync 直接运行 agent；mode=async 经 tasks 服务建任务
（TaskService.create + enqueue_task 投递 Celery tasks.execute）。
tasks 模块缺失时 async 自动回退为同步执行（warnings 中注明）。
模块 import 时向本模块 agent 注册表注册 agriculture_qa。
"""

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.core.deps import TenantContext, get_db_session, get_tenant_context
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

log = get_logger(__name__)
router = APIRouter(prefix="/qa", tags=["agriculture-qa"])

# tasks 服务（真实模块已就位；缺失时 async 降级为同步）
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
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    agent_ctx = QAAgentContext(
        tenant_id=ctx.tenant_id,
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
            # broker 不可用时 enqueue_task 返回 False，任务保持 queued 可稍后补投递
            enqueued = enqueue_task(task.id) if enqueue_task is not None else False
            log_audit(
                db,
                tenant_id=ctx.tenant_id,
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
        # tasks 模块缺失：回退同步，诚实告知（下文同步分支会追加 warnings）

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

    log_audit(
        db,
        tenant_id=ctx.tenant_id,
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
