"""任务类型处理器注册表。

Handler 签名：``handler(input_data: dict, context: dict) -> dict``

- input_data：任务输入（Task.input_json["data"]）。
- context：{"task_id", "yard_id", "actor_id", "request_id"}。
- 返回：写入 Task.result_json 的结果字典。

其他模块用 @register_handler("my.task_type") 注册自己的处理器。
"""

from collections.abc import Callable
from typing import Any

TaskHandler = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]

TASK_HANDLERS: dict[str, TaskHandler] = {}


def register_handler(task_type: str) -> Callable[[TaskHandler], TaskHandler]:
    """注册任务类型处理器。重复注册同一类型时后者覆盖前者。"""

    def decorator(fn: TaskHandler) -> TaskHandler:
        TASK_HANDLERS[task_type] = fn
        return fn

    return decorator


@register_handler("mock.echo")
def echo_handler(input_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """示例/测试用处理器：原样回显输入。"""
    return {"echo": input_data, "task_id": context.get("task_id")}


def _load_builtin_handlers() -> None:
    """导入各业务模块的处理器实现，触发 ``@register_handler`` 注册。

    Celery worker 进程只 import 本模块；若不在启动时主动导入注册方，
    真实任务（team_run / knowledge_qa / factory …）在 worker 侧一律
    "未注册的任务类型"。API 进程因为 import 全部 router 碰巧正常，
    问题只在 worker 暴露，属于典型的"本机能跑、部署必挂"。
    """
    import importlib

    for path in (
        "app.modules.agents.teams.worker_tasks",
        "app.modules.agriculture.worker_tasks",
        "app.modules.agents.agent_generate_tasks",
        "app.modules.agents.factory_pipeline",
        "app.modules.org.proposal_tasks",
    ):
        try:
            importlib.import_module(path)
        except Exception:  # noqa: BLE001 - 单个模块导入失败不应拖垮其余处理器
            import logging

            logging.getLogger(__name__).warning("导入任务处理器失败：%s", path, exc_info=True)


_load_builtin_handlers()
