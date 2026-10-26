"""推送通道服务：CRUD + 事件发送 dispatch。

发送纪律（复刻 devices 连接器三件套）：
- **SSRF 双检**：保存/换 URL 时 ``check_egress_url`` 校验一次，发送前再检一次
  （配置后域名背后 IP 可能已变——DNS 重绑定防线）；DNS 解析失败由 net_guard
  放行给 HTTP 客户端自然报"不可达"，本模块记失败、不重试轰炸。
- **熔断**：连续失败 ``PUSH_MAX_FAILURES`` 次 → ``enabled=false`` + 审计；
  成功一次即清零；重填 URL 也清零（视同重新开始）。
- **限频**：每通道 60s 滑窗最多 ``PUSH_MAX_MESSAGES_PER_WINDOW`` 条，超限静默跳过。
  **诚实边界**：滑窗是进程内实现，多实例部署各自独立计数（与 devices 限频、
  core/rate_limit 同一口径）；分布式限频是扩容前置项。

``dispatch`` 对主流程**永不抛异常**：整体 try/except，失败只写日志/审计/计数。
"""

import threading
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.net_guard import check_egress_url
from app.modules.audit.service import log_audit
from app.modules.push.models import EVENT_LABELS, VALID_EVENTS, VALID_KINDS, PushChannel

log = get_logger(__name__)

#: 连续失败自动停用阈值（对齐 devices 连接器 CONNECTOR_MAX_FAILURES）
PUSH_MAX_FAILURES = 5
#: 每通道限频滑窗：60 秒内最多 10 条
PUSH_WINDOW_SECONDS = 60
PUSH_MAX_MESSAGES_PER_WINDOW = 10
#: 单条推送 HTTP 超时（秒）：事件发送在请求路径上，宁可慢 5s 不可挂死
PUSH_TIMEOUT_SECONDS = 5


# ---------------------------------------------------------------------------
# 限频（进程内滑动窗口；多实例部署需换 Redis，与 devices 同一诚实边界）
# ---------------------------------------------------------------------------


class _PushWindow:
    """按通道 id 统计窗口内已发条数：先 check 再 record（超限静默跳过）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        """允许则立刻记一次并返回 True；超限返回 False（不发不记）。"""
        now = time.monotonic()
        with self._lock:
            dq = self._hits.setdefault(key, deque())
            cutoff = now - PUSH_WINDOW_SECONDS
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= PUSH_MAX_MESSAGES_PER_WINDOW:
                return False
            dq.append(now)
            return True

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)


_rate_window = _PushWindow()


# ---------------------------------------------------------------------------
# URL 加密 / 掩码（URL 含 key 视同密钥：加密复用 supplier_crypto，掩码只留协议+主机）
# ---------------------------------------------------------------------------


def mask_url(url: str) -> str:
    """掩码形态：``scheme://host/***``——key 在 path/query，一律不出接口。"""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return "****"
    return f"{parsed.scheme}://{parsed.netloc}/***"


def prepare_url_enc(url: str) -> str:
    """保存/换 URL 入口：SSRF 校验（第一重）→ Fernet 加密，返回入库密文。"""
    from app.modules.llm.supplier_crypto import encrypt_api_key

    check_egress_url(url, context="push_channel")
    return encrypt_api_key(url)


def decrypt_url(channel: PushChannel) -> str:
    from app.modules.llm.supplier_crypto import decrypt_api_key

    assert channel.url_enc is not None  # 调用方已判空
    return decrypt_api_key(channel.url_enc)


def channel_to_out(ch: PushChannel) -> dict[str, Any]:
    """出参：URL 只有掩码（解密失败也不回显任何密文/明文片段）。"""
    url_masked: str | None = None
    if ch.url_enc:
        try:
            url_masked = mask_url(decrypt_url(ch))
        except AppError:
            url_masked = "****（无法解密，请重新录入 URL）"
    return {
        "id": ch.id,
        "yard_id": ch.yard_id,
        "name": ch.name,
        "kind": ch.kind,
        "url_masked": url_masked,
        "events": list(ch.events_json or []),
        "enabled": ch.enabled,
        "consecutive_failures": ch.consecutive_failures,
        "created_by": ch.created_by,
        "created_at": ch.created_at.isoformat() if ch.created_at else None,
        "updated_at": ch.updated_at.isoformat() if ch.updated_at else None,
    }


# ---------------------------------------------------------------------------
# 入参校验（kind/events 白名单，宁严勿松）
# ---------------------------------------------------------------------------


def validate_kind(kind: str) -> None:
    if kind not in VALID_KINDS:
        raise AppError(
            "INVALID_REQUEST",
            f"kind 必须是 {'|'.join(VALID_KINDS)} 之一。",
            http_status=400,
        )


def validate_events(events: list[str]) -> None:
    for ev in events:
        if ev not in VALID_EVENTS:
            raise AppError(
                "INVALID_REQUEST",
                f"订阅事件必须是 {'|'.join(VALID_EVENTS)} 中的 code。",
                http_status=400,
            )


def get_channel_or_404(db: Session, yard_id: str, channel_id: str) -> PushChannel:
    """跨小院与不存在统一 404（不泄露其他小院通道是否存在）。"""
    ch = db.get(PushChannel, channel_id)
    if ch is None or ch.yard_id != yard_id:
        raise AppError("NOT_FOUND", "推送通道不存在。", http_status=404)
    return ch


# ---------------------------------------------------------------------------
# 报文构建（wecom/dingtalk markdown 中文字段；generic 原样 {event,payload,ts}）
# ---------------------------------------------------------------------------


def _markdown_text(event_type: str, payload: dict[str, Any]) -> str:
    """中文 markdown 正文：事件标签 + 标题 + 其余字段逐行列表。"""
    label = EVENT_LABELS.get(event_type, event_type)
    title = str(payload.get("title") or label)
    lines = [f"### {label}", f"**{title}**"]
    for key, value in payload.items():
        if key == "title":
            continue
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def build_body(kind: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """按通道类型构建 POST JSON 报文。"""
    if kind == "generic_webhook":
        tz = datetime.now(UTC)
        return {"event": event_type, "payload": payload, "ts": tz.isoformat()}
    content = _markdown_text(event_type, payload)
    if kind == "dingtalk":
        label = EVENT_LABELS.get(event_type, event_type)
        return {"msgtype": "markdown", "markdown": {"title": label, "text": content}}
    # wecom_robot
    return {"msgtype": "markdown", "markdown": {"content": content}}


# ---------------------------------------------------------------------------
# 事件发送（主入口）
# ---------------------------------------------------------------------------


def _post_json(url: str, body: dict[str, Any]) -> int:
    """发送一次 JSON POST，返回状态码；网络/HTTP 错误抛异常（由 dispatch 计数）。"""
    with httpx.Client(timeout=PUSH_TIMEOUT_SECONDS) as client:
        resp = client.post(url, json=body)
        resp.raise_for_status()
        return resp.status_code


def _record_failure(db: Session, ch: PushChannel, reason: str, request_id: str) -> None:
    """失败计数 + 熔断；调用方保证不抛出（log_audit 自带护栏）。"""
    ch.consecutive_failures += 1
    log.warning(
        "push channel %s failed (%s/%s): %s",
        ch.id[:8],
        ch.consecutive_failures,
        PUSH_MAX_FAILURES,
        reason,
    )
    log_audit(
        db,
        yard_id=ch.yard_id,
        actor_id=None,
        action="push_channel_failed",
        resource_type="push_channel",
        resource_id=ch.id,
        result="failed",
        request_id=request_id,
        metadata={"failures": ch.consecutive_failures, "reason": reason[:200]},
    )
    if ch.consecutive_failures >= PUSH_MAX_FAILURES:
        ch.enabled = False
        log.warning(
            "push channel %s 连续 %s 次失败，已自动停用", ch.id[:8], ch.consecutive_failures
        )
        log_audit(
            db,
            yard_id=ch.yard_id,
            actor_id=None,
            action="push_channel_disabled",
            resource_type="push_channel",
            resource_id=ch.id,
            result="success",
            request_id=request_id,
            metadata={
                "name": ch.name,
                "reason": (
                    f"连续 {PUSH_MAX_FAILURES} 次推送失败，已自动停用，请检查 URL 与网络后重新启用"
                ),
            },
        )


def dispatch(
    db: Session,
    *,
    yard_id: str,
    event_type: str,
    payload: dict[str, Any],
    request_id: str = "",
) -> dict[str, int]:
    """向该小院订阅了 ``event_type`` 的启用通道逐条推送。

    **发送失败绝不影响主流程**：整体 try/except（log_audit/db.add 只挂进调用方
    事务；外层若回滚，推送已发出但计数一并回滚——多实例语义一致，诚实接受）。
    返回统计 {sent, failed, skipped}；任何内部异常都被吞掉并计入日志。
    """
    stats = {"sent": 0, "failed": 0, "skipped": 0}
    try:
        channels = list(
            db.scalars(
                select(PushChannel).where(
                    PushChannel.yard_id == yard_id,
                    PushChannel.enabled.is_(True),
                )
            )
        )
        for ch in channels:
            if event_type not in (ch.events_json or []):
                continue
            if not ch.url_enc:
                stats["skipped"] += 1
                continue
            if not _rate_window.allow(ch.id):
                stats["skipped"] += 1
                log.debug("push rate limited: channel=%s event=%s", ch.id[:8], event_type)
                continue
            try:
                url = decrypt_url(ch)
            except AppError as e:
                # 密文不可解（主密钥轮换等）：记失败但不做熔断轰炸（每事件最多一次）
                stats["failed"] += 1
                _record_failure(db, ch, f"URL 解密失败：{e.message}", request_id)
                continue
            try:
                # 发送前复检 SSRF（第二重）：配置后 DNS 可能已变
                check_egress_url(url, context=f"push_channel:{ch.id[:8]}")
                body = build_body(ch.kind, event_type, payload)
                _post_json(url, body)
            except Exception as e:  # noqa: BLE001 —— 推送失败与主流程彻底隔离
                stats["failed"] += 1
                _record_failure(
                    db, ch, str(getattr(e, "message", None) or type(e).__name__)[:200], request_id
                )
                continue
            ch.consecutive_failures = 0
            stats["sent"] += 1
        db.flush()
    except Exception as e:  # noqa: BLE001 —— dispatch 永不影响业务主流程
        log.warning(
            "push dispatch crashed (ignored): yard=%s event=%s err=%s",
            yard_id,
            event_type,
            e,
        )
    return stats
