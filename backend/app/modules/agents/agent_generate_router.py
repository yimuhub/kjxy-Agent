"""一句话生成智能体 REST API（/agents/generate*，挂载于 agents 路由之下）。

- POST /agents/generate/preview：同步轻量预览（不调 LLM）：意图关键词 + 现有
  技能/插件候选 + 配额快照，供向导第一步即时反馈。
- POST /agents/generate：建 ``agent_generate`` 任务并立即返回 202 {task_id}；
  真正的检索/设计/匹配/落库在 agent_generate_tasks 执行，前端用现成的
  GET /tasks/{task_id}/events/stream 追进度（四阶段里程碑）。
- GET  /agents/generate/policy：当前成员的配额快照（enabled/used/limit）。

权限：不新增权限点，沿用「创建声明式 Agent」口径——任何小院成员可发起；
visibility=public 需要管理员（请求侧 403，见 agent_builder.ensure_visibility_allowed）。
成本闸门：tasks 配额（check_and_consume）+ 小院设置 agent_generate.daily_limit
（每成员每日，按 tasks 行计数，超限 429）。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.deps import (
    CurrentUser,
    YardContext,
    get_current_user,
    get_db_session,
    get_yard_context,
)
from app.core.responses import ok
from app.modules.agents import (
    agent_builder,
    agent_generate_tasks,  # noqa: F401 - 注册任务 handler
)
from app.modules.audit.service import log_audit
from app.modules.tasks.router import _enqueue_or_inline
from app.modules.tasks.service import TaskService
from app.modules.yards.quota import check_and_consume

router = APIRouter(prefix="/generate", tags=["agents-generate"])

_UTTERANCE_PREVIEW_CHARS = 40
_IDEMPOTENCY_WINDOW_SECONDS = 30


def _generate_idempotency_key(yard_id: str, user_id: str, utterance: str) -> str:
    """内容哈希 + 30 秒时间桶。

    ``tasks`` 在 (yard_id, idempotency_key) 上有唯一约束，且 ``TaskService.create``
    命中同键即返回旧任务：键若不含时间，上一轮已结束的旧草稿会永久吃掉同一句话的
    「重新生成」（还会让新任务撞唯一约束）。
    """
    bucket = int(datetime.now(UTC).timestamp()) // _IDEMPOTENCY_WINDOW_SECONDS
    digest = hashlib.sha1(f"{yard_id}:{user_id}:{utterance}:{bucket}".encode()).hexdigest()
    return f"agent-gen-{digest[:16]}"


class AgentGeneratePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    utterance: str = Field(min_length=1, max_length=2000)


class AgentGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    utterance: str = Field(min_length=1, max_length=2000)
    visibility: str = Field(default="private", max_length=10)
    use_web: bool = True
    supplier_id: str | None = Field(default=None, max_length=36)


@router.post("/preview")
def preview(
    payload: AgentGeneratePreviewRequest,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """同步预览：意图关键词 + 现有技能/插件候选 + 配额快照（不调 LLM、不落库）。"""
    result = agent_builder.request_agent(db, ctx=ctx, utterance=payload.utterance)
    return ok(result, request)


@router.post("")
def generate(
    payload: AgentGenerateRequest,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """发起一句话生成：建任务并立即返回句柄（202）。

    幂等：同小院同人同 utterance 30 秒内重复提交只算一次任务（idempotency_key）。
    """
    visibility = agent_builder.ensure_visibility_allowed(
        visibility=payload.visibility, roles=list(ctx.roles or [])
    )
    # 双闸：tasks 全局配额（429 QUOTA_EXCEEDED）+ 每人每日生成上限（429 AGENT_GENERATE_QUOTA）
    check_and_consume(db, ctx.yard_id, "tasks")
    agent_builder.ensure_daily_quota(db, ctx.yard_id, user.user_id)

    task = TaskService.create(
        db,
        ctx,
        task_type=agent_builder.TASK_TYPE,
        input={
            "utterance": payload.utterance.strip(),
            "visibility": visibility,
            "use_web": payload.use_web,
            "supplier_id": payload.supplier_id,
            "roles": list(ctx.roles or []),
        },
        request_id=ctx.request_id,
        idempotency_key=_generate_idempotency_key(
            ctx.yard_id, user.user_id, payload.utterance.strip()
        ),
    )
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=user.user_id,
        action="agent_generate_requested",
        resource_type="task",
        resource_id=task.id,
        result="success",
        request_id=ctx.request_id,
        metadata={
            "task_id": task.id,
            "visibility": visibility,
            "utterance_preview": payload.utterance.strip()[:_UTTERANCE_PREVIEW_CHARS],
        },
    )
    db.commit()
    _enqueue_or_inline(db, task)
    return JSONResponse(
        status_code=202,
        content=ok({"task_id": task.id}, request),
    )


@router.get("/policy")
def policy(
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """当前成员的生成配额快照（向导第一步展示还能生成几次）。"""
    snapshot: dict[str, Any] = agent_builder.quota_snapshot(db, ctx.yard_id, user.user_id)
    return ok(snapshot, request)
