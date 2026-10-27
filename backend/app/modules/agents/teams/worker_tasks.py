"""班组任务处理器：仅接受台账 ID，身份与权限从数据库重建。"""

import asyncio
from typing import Any

from app.core.errors import AppError
from app.modules.agents.teams import service
from app.modules.tasks.handlers import register_handler
from app.modules.tasks.models import Task
from app.modules.tasks.worker_tasks import _get_session_factory


@register_handler("team_run")
def run_team_task(input_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    db = _get_session_factory()()
    try:
        task = db.get(Task, str(context.get("task_id") or ""))
        run_id = str(input_data.get("run_id") or "")
        if (
            task is None
            or task.task_type != "team_run"
            or task.status != "running"
            or task.yard_id != context.get("yard_id")
            or task.created_by != context.get("actor_id")
            or (task.input_json or {}).get("data") != {"run_id": run_id}
        ):
            raise AppError("TEAM_TASK_CONTEXT_INVALID", "任务身份或输入不匹配。", http_status=403)
        run = service.get_team_run(db, yard_id=task.yard_id, run_id=run_id, user_id=task.created_by)
        if run.task_id != task.id:
            raise AppError("TEAM_TASK_CONTEXT_INVALID", "任务未绑定此运行。", http_status=403)
        # 后台执行不继承签发时的角色：按当前账号与小院成员关系重建执行身份。
        try:
            ctx = service.execution_context(
                db,
                yard_id=task.yard_id,
                user_id=task.created_by,
                request_id=str(context.get("request_id") or "req_team_worker"),
            )
        except AppError as exc:
            service.mark_run_failed(db, run, f"{exc.code}: {exc.message}")
            raise
        asyncio.run(service.continue_team_run(db, run=run, ctx=ctx))
        # 通用 tasks API 为院内可读，不在任务结果复制私有答案与研究资料。
        return {"run_id": run.id, "status": run.status}
    finally:
        db.close()
