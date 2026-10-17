"""日志：标准库 logging，统一格式并携带 request_id；禁止输出密钥。"""

import logging
import sys

from app.core.context import request_id_var

_configured = False


class _RequestIdFilter(logging.Filter):
    """把 contextvar 中的 request_id 注入每条日志记录（规范 §16.1 关联字段）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        return True


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] [%(request_id)s] %(message)s")
    )
    handler.addFilter(_RequestIdFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # 访问日志保留（排障/取证依赖）
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
