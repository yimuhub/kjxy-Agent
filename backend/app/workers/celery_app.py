"""Celery 应用。后台任务入口见各模块的 workers 实现。"""

from celery import Celery
from celery.app import trace as celery_trace
from celery.signals import worker_process_init
from celery.utils.nodenames import gethostname
from kombu.serialization import prepare_accept_content

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


def _populate_trace_localized(app: Celery) -> bool:
    """在**子进程**里补齐 celery 的 trace 快速路径上下文。

    ``celery/app/trace.py`` 的 ``fast_trace_task`` 依赖模块级 ``_localized``：

        tasks, accept, hostname = _loc      # _loc == [] → ValueError

    父进程启动时会调 ``setup_worker_optimizations()`` 把它填好。fork 子进程
    继承父进程内存 → 天然有值；**spawn 子进程重新 import celery → 是空的**，
    于是每一个任务都抛 ``ValueError: not enough values to unpack``，
    后台任务全线静默失败（macOS / Windows 的默认行为，生产事故 D-02）。

    Celery 自己在 ``celery/concurrency/prefork.py`` 里写的是
    ``trace._tasks = app._tasks``（旧版 fast_trace_task 的入口），与 5.x 的
    ``_localized`` 不匹配 —— 上游缺陷，只能在应用侧兜住。

    这里在 worker 子进程初始化时按 celery 自己的口径补齐，返回是否补齐成功。
    """
    if getattr(celery_trace, "_localized", None):
        return False  # fork 子进程已继承，无需处理
    try:
        celery_trace._localized[:] = [
            app._tasks,
            prepare_accept_content(app.conf.accept_content),
            gethostname(),
        ]
    except Exception as e:  # noqa: BLE001 - 补齐失败也不该让 worker 进程起不来
        log.warning("补齐 celery trace._localized 失败（任务将退回标准执行路径）: %s", e)
        return False
    log.info("spawn worker 子进程已补齐 celery trace 快速路径上下文")
    return True


@worker_process_init.connect
def _on_worker_process_init(sender=None, **kwargs) -> None:
    """worker 子进程启动钩子：spawn 平台补齐 trace 快速路径上下文。"""
    from celery import current_app

    app = current_app or celery_app
    _populate_trace_localized(app)


class TechYardCelery(Celery):
    """平台自适应的 Celery 应用。

    ``use_fast_trace_task`` 改成**按运行时状态计算**的属性：
    只有 ``trace._localized`` 真的被装配时才允许走快速路径。
    父进程启动时 celery 会把它写 True（``setup_worker_optimizations``），
    但子进程是否能用这个快速路径取决于它自己有没有 ``_localized``——
    spawn 子进程由 :func:`_populate_trace_localized` 在初始化时补齐，
    fork 子进程天然继承。因此不再由 celery 单方面决定，避免
    "父进程说可以、子进程解包失败" 的静默故障。
    """

    @property
    def use_fast_trace_task(self) -> bool:
        """仅在 celery 的 trace 快速路径确实可用时才启用。"""
        return bool(getattr(celery_trace, "_localized", None))

    @use_fast_trace_task.setter
    def use_fast_trace_task(self, value: bool) -> None:
        # celery 内部（setup_worker_optimizations）会写这个标志；
        # 这里不落库，真实取值由 getter 依据运行时状态决定。
        return None


celery_app = TechYardCelery(
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
        # 逾期工单升级（v3.10）：每 6 小时巡检，避免每天只升一次响应过慢
        "workorders-escalate-overdue": {
            "task": "workorders.escalate_overdue",
            "schedule": 21600.0,
        },
        # 对话内视频（v3.73）：5 秒续查在途任务，每小时清理过期产物
        "media-poll-videos": {"task": "media.poll_videos", "schedule": 5.0},
        "media-expire": {"task": "media.expire", "schedule": 3600.0},
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
        "app.modules.media",
    ]
)
