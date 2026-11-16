"""滑动窗口限流器（Redis 分布式优先 + 进程内内存自动兜底）与受信客户端 IP 提取。

用于公开端点的"公共限流"（如登录接口、配对码交换、SSO、A2A RPC、公开分享等）：
- 生产多进程 / 多实例部署且 Redis 可用时，使用 Redis 原子计数，跨进程共享窗口；
- 桌面单机模式（TASKS_INLINE_MODE）、测试环境或 Redis 瞬时不可达时，
  无感回退至进程内线程安全内存滑动窗口。
"""

import threading
import time
from collections import deque
from typing import Any

from app.core.config import settings
from fastapi import Request


def extract_client_ip(request: Request) -> str:
    """统一提取限流键使用的客户端真实 IP（遵循 S-8 受信反代链规范）。

    - 直连部署（``trusted_proxy_count == 0``）直接取 socket 对端 IP；
    - 反代部署（``trusted_proxy_count == N``）取 ``X-Forwarded-For`` 倒数第 N 跳，
      既防止攻击者伪造 XFF 首跳绕过限流，又避免 Nginx 反代下所有用户共用网关 IP 导致误锁全站。
    """
    socket_ip = request.client.host if request.client else None
    proxy_hops = max(0, int(getattr(settings, "trusted_proxy_count", 0) or 0))
    if proxy_hops == 0:
        return socket_ip or "unknown"
    if socket_ip == "testclient":
        return socket_ip
    forwarded = request.headers.get("x-forwarded-for", "")
    parts = [p.strip() for p in forwarded.split(",") if p.strip()]
    if len(parts) >= proxy_hops:
        return parts[-proxy_hops]
    return socket_ip or "unknown"


class RateLimiter:
    """支持 Redis 集中计数与内存回退的滑动窗口限流器。"""

    def __init__(self, max_attempts: int, window_seconds: int, namespace: str = "rl"):
        if max_attempts <= 0:
            raise ValueError("max_attempts 必须为正整数")
        if window_seconds <= 0:
            raise ValueError("window_seconds 必须为正整数")
        self._max_attempts = max_attempts
        self._window = float(window_seconds)
        self._window_int = int(window_seconds)
        self._ns = namespace
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = {}

    def _redis_key(self, key: str) -> str:
        return f"techyard:{self._ns}:{self._window_int}:{key}"

    def _redis_client(self) -> Any:
        if settings.tasks_inline_mode or settings.app_env != "production":
            return None
        try:
            import redis

            return redis.Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=0.3,
                socket_timeout=0.3,
                decode_responses=True,
            )
        except Exception:
            return None

    def is_allowed(self, key: str) -> bool:
        """窗口内失败次数未达上限时返回 True。"""
        client = self._redis_client()
        if client is not None:
            try:
                raw = client.get(self._redis_key(key))
                count = int(raw) if raw is not None else 0
                return count < self._max_attempts
            except Exception:
                pass
        now = time.monotonic()
        with self._lock:
            bucket = self._prune(key, now)
            return bucket is None or len(bucket) < self._max_attempts

    def record_failure(self, key: str) -> None:
        """记录一次失败（失败本身会计入限流；限流检查超限不计次）。"""
        client = self._redis_client()
        if client is not None:
            try:
                rkey = self._redis_key(key)
                pipe = client.pipeline()
                pipe.incr(rkey)
                pipe.expire(rkey, self._window_int, nx=True)
                pipe.execute()
                return
            except Exception:
                pass
        now = time.monotonic()
        with self._lock:
            bucket = self._prune(key, now)
            if bucket is None:
                bucket = deque()
                self._failures[key] = bucket
            bucket.append(now)

    def reset(self, key: str) -> None:
        """清零该 key 的失败计数（通常在成功时调用）。"""
        client = self._redis_client()
        if client is not None:
            try:
                client.delete(self._redis_key(key))
            except Exception:
                pass
        with self._lock:
            self._failures.pop(key, None)

    def _prune(self, key: str, now: float) -> deque[float] | None:
        """清理该 key 窗口外的失败记录。"""
        bucket = self._failures.get(key)
        if bucket is None:
            return None
        cutoff = now - self._window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if not bucket:
            del self._failures[key]
            return None
        return bucket
