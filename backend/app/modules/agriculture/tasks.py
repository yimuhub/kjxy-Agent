"""Celery 任务发现入口。

app/workers/celery_app.py 的 autodiscover_tasks 按 ``<package>.tasks`` 约定查找，
此处做一层转发，保证 qa.answer 能被 worker 发现；
knowledge_qa 处理器的注册在 worker_tasks import 时完成。
"""

from app.modules.agriculture.worker_tasks import (  # noqa: F401
    knowledge_qa_handler,
    qa_answer_task,
    run_knowledge_qa,
)
