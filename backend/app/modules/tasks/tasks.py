"""Celery 任务发现入口。

celery_app.autodiscover_tasks(["app.modules.tasks"]) 按约定查找本包下的
`tasks` 子模块；实际实现在 worker_tasks.py，本文件仅做转发。
"""

from app.modules.tasks.worker_tasks import enqueue_task, execute_task  # noqa: F401
