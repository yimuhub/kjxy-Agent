"""Celery 应用。后台任务入口见各模块的 workers 实现。"""

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "tech_yard",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    # beat 定时任务所在模块：worker/beat 启动时强制导入以注册任务
    include=["app.workers.heartbeat_tasks"],
)

celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    timezone="UTC",
    enable_utc=True,
    # 规范 §7.5：超时回收每 5 分钟；§18.4：worker 心跳每 60 秒
    beat_schedule={
        "reap-stale-tasks": {"task": "tasks.reap_stale", "schedule": 300.0},
        "worker-heartbeat": {"task": "workers.heartbeat", "schedule": 60.0},
        # 每日检查一次“本月是否已清零”（幂等 Redis 键，见 heartbeat_tasks.quota_monthly_reset）
        "quota-monthly-reset": {"task": "quota.monthly_reset", "schedule": 86400.0},
        # 设备连接器轮询：beat 固定 60s 心跳节奏，各设备按自身 interval 判定到期
        "devices-poll-connectors": {"task": "devices.poll_connectors", "schedule": 60.0},
        # 自我总结提升（v3.4）：beat 每 5 分钟巡检，各小院按 next_run_at 判定到期
        "yards-run-due-jobs": {"task": "yards.run_due_jobs", "schedule": 300.0},
    },
)

# 自动发现各模块的 celery 任务（agents 提供 agent_factory 工厂任务的 handler 注册；
# devices 提供连接器轮询任务，桌面 inline 模式见 devices.worker_tasks）
celery_app.autodiscover_tasks(
    [
        "app.modules.tasks",
        "app.modules.knowledge",
        "app.modules.agriculture",
        "app.modules.agents",
        "app.modules.devices",
        "app.modules.self_improve",
    ]
)
