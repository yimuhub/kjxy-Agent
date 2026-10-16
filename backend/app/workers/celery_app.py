"""Celery 应用。后台任务入口见各模块的 workers 实现。"""

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "tech_yard",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    timezone="UTC",
    enable_utc=True,
)

# 自动发现各模块的 celery 任务
celery_app.autodiscover_tasks(
    ["app.modules.tasks", "app.modules.knowledge", "app.modules.agriculture"]
)
