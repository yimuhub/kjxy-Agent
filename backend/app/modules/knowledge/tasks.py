"""Celery 任务发现入口。

app/workers/celery_app.py 的 autodiscover_tasks 按 ``<package>.tasks`` 约定查找，
此处做一层转发，保证 knowledge.parse_document 能被 worker 发现。
"""

from app.modules.knowledge.worker_tasks import parse_document  # noqa: F401
