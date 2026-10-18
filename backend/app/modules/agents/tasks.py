"""Agent 工厂任务的 Celery 自动发现入口。

Celery worker 启动时（celery.autodiscover_tasks 对 app.modules.agents 扫描
同名 tasks 模块）import 本模块 → 触发 factory_pipeline 的注册副作用，
保证 worker 进程内 TASK_HANDLERS 里有 agent_factory。
API 进程走 agents.router → factory_router → factory_pipeline 的同一条注册链。
"""

from app.modules.agents.factory_pipeline import (  # noqa: F401 - 注册 agent_factory handler
    run_agent_factory,
)

__all__ = ["run_agent_factory"]
