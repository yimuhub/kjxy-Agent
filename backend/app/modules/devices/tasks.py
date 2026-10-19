"""Celery 任务发现入口（autodiscover 按 ``<package>.tasks`` 约定）。

转发 devices.worker_tasks 中的连接器轮询任务，保证 worker/beat 进程能发现
``devices.poll_connectors``；任务实现见 worker_tasks.py。
"""

from app.modules.devices.worker_tasks import (  # noqa: F401
    poll_connectors_task,
    run_inline_poll_once,
)
