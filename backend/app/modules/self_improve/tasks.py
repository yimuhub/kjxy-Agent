"""Celery 任务发现入口（autodiscover 按 ``<package>.tasks`` 约定）。

转发 self_improve.worker_tasks 中的到期任务巡检，保证 worker/beat 进程能发现
``yards.run_due_jobs``；任务实现见 worker_tasks.py。
"""

from app.modules.self_improve.worker_tasks import (  # noqa: F401
    run_due_jobs_task,
    start_inline_scheduler,
)
