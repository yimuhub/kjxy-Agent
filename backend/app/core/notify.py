"""运维告警通道（规范 §16.2）。

统一入口 send_alert()：配置了 ALERT_WEBHOOK_URL 就 POST JSON（fire-and-forget 线程，
超时 5s，失败只写日志绝不影响业务）；未配置时降级为带 [ALERT] 标记的 WARNING 日志，
保证单机试点也能靠日志采集发现配额/任务/入库异常。
"""

import json
import threading
import urllib.error
import urllib.request

from app.core.config import settings
from app.core.context import metrics
from app.core.logging import get_logger

log = get_logger(__name__)


def send_alert(title: str, message: str, *, level: str = "warning", dedupe_key: str = "") -> bool:
    """发送一条运维告警。返回是否成功推送 webhook（未配置 webhook 返回 False）。

    dedupe_key：同一进程内相同 key 只推送一次，防止周期性任务刷屏。
    """
    metrics.observe_alert(level)
    if dedupe_key:
        with _dedupe_lock:
            if dedupe_key in _deduped:
                log.debug("alert deduped: %s", dedupe_key)
                return False
            _deduped.add(dedupe_key)
    if not settings.alert_webhook_url:
        log.warning("[ALERT][%s] %s | %s", level, title, message)
        return False
    payload = json.dumps(
        {"level": level, "title": title, "message": message, "service": settings.app_name}
    ).encode("utf-8")

    def _post() -> None:
        req = urllib.request.Request(
            settings.alert_webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status >= 300:
                    log.warning("alert webhook returned HTTP %s", resp.status)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            log.warning(
                "[ALERT-FALLBACK][%s] %s | %s (webhook failed: %s)", level, title, message, e
            )

    threading.Thread(target=_post, daemon=True, name="alert-webhook").start()
    return True


_deduped: set[str] = set()
_dedupe_lock = threading.Lock()
