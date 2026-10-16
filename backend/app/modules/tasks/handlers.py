"""任务类型处理器注册表。

Handler 签名：``handler(input_data: dict, context: dict) -> dict``

- input_data：任务输入（Task.input_json["data"]）。
- context：{"task_id", "tenant_id", "actor_id", "request_id"}。
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
