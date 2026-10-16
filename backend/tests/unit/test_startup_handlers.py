"""启动装配冒烟测试：handler / 工具注册完整性。

规范 §7.4 规定创建任务的请求示例 task_type=knowledge_qa；无论部署形态
（Docker worker / native 同步降级线程）都必须能执行。native 模式下
knowledge_qa 的 handler 注册依赖 main._startup_wiring 的 import 副作用，
本测试防止装配被删改后回归（P0-2 事故的防复发测试）。
"""

from app.modules.tasks.handlers import TASK_HANDLERS


def test_builtin_task_handlers_registered():
    # 导入链：tasks（mock.echo）+ agriculture（knowledge_qa）+ workorder（workorder.dispatch）
    import app.main  # noqa: F401  触发 create_app 定义
    from app.main import _startup_wiring

    _startup_wiring()  # 内部 try/except，DB 不可用时只告警；import 副作用必须发生

    expected = {"mock.echo", "knowledge_qa", "workorder.dispatch"}
    missing = expected - set(TASK_HANDLERS)
    assert (
        not missing
    ), f"缺少已注册的任务 handler：{missing}（TASK_HANDLERS={list(TASK_HANDLERS)}）"


def test_builtin_tools_registered():
    from app.modules.tools.registry import tool_registry

    codes = {t.code for t in tool_registry.list_tools()}
    assert {"knowledge.search", "workorder.dispatch"} <= codes, f"工具注册表缺少内置工具：{codes}"
