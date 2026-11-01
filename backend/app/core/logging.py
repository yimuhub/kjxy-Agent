"""日志：标准库 logging，统一格式并携带 request_id；禁止输出密钥。

除控制台外同时落盘 ``<data_dir>/logs/app.log``（10MB × 5 轮转），系统设置里的
「系统日志」直接读它排障；桌面版 sidecar 的 ``engine.log`` 作为第二个来源。
"""

import logging
import logging.handlers
import os
import sys
from pathlib import Path

from app.core.context import request_id_var

_configured = False
_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] [%(request_id)s] %(message)s"


class _RequestIdFilter(logging.Filter):
    """把 contextvar 中的 request_id 注入每条日志记录（规范 §16.1 关联字段）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        return True


def log_dir() -> Path:
    """日志目录：桌面独立部署用 TECHYARD_DATA_DIR/logs，源码运行用 backend/data/logs。"""
    raw = os.environ.get("TECHYARD_DATA_DIR", "").strip()
    if raw:
        return Path(raw).expanduser() / "logs"
    return Path(__file__).resolve().parents[2] / "data" / "logs"


def log_paths() -> dict[str, Path]:
    """可供「系统日志」查看的文件：应用日志 + 桌面引擎日志。"""
    directory = log_dir()
    return {
        "app": directory / "app.log",
        "engine": directory / "engine.log",
    }


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    formatter = logging.Formatter(_LOG_FORMAT)
    request_filter = _RequestIdFilter()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(request_filter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 落盘：写失败（只读目录/权限）不能拖垮启动，退回纯控制台
    try:
        directory = log_dir()
        directory.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            str(directory / "app.log"),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(request_filter)
        root.addHandler(file_handler)
        # uvicorn 自己的 logger 不向 root 传播，单独挂同一份文件处理器
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            logging.getLogger(name).addHandler(file_handler)
    except Exception as exc:  # noqa: BLE001 - 日志落盘失败不阻断启动
        logging.getLogger(__name__).warning("日志文件不可写，仅输出到控制台: %s", exc)

    # 访问日志保留（排障/取证依赖）
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
