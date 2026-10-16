"""内存滑动窗口限流器（线程安全）。

用于公开端点的"公共限流"（如登录接口按 IP+用户名限流）：
只统计失败事件 —— is_allowed(key) 判断窗口内失败次数是否已达上限；
成功时调用 reset(key) 清零；record_failure(key) 记录一次失败。

注意：这是进程内内存实现，多进程/多实例部署时需换 Redis 集中计数。
"""

import threading
import time
from collections import deque


class RateLimiter:
    """滑动窗口限流器。

    每个 key 独立维护一个"失败时间戳"队列；窗口外的记录在每次读写时
    惰性清理（_prune），避免单独的后台清理线程。
    """

    def __init__(self, max_attempts: int, window_seconds: int):
        if max_attempts <= 0:
            raise ValueError("max_attempts 必须为正整数")
        if window_seconds <= 0:
            raise ValueError("window_seconds 必须为正整数")
        self._max_attempts = max_attempts
        self._window = float(window_seconds)
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = {}

    def is_allowed(self, key: str) -> bool:
        """窗口内失败次数未达上限时返回 True。"""
        now = time.monotonic()
        with self._lock:
            bucket = self._prune(key, now)
            return bucket is None or len(bucket) < self._max_attempts

    def record_failure(self, key: str) -> None:
        """记录一次失败（失败本身会计入限流；限流检查超限不计次）。"""
        now = time.monotonic()
        with self._lock:
            bucket = self._prune(key, now)
            if bucket is None:
                bucket = deque()
                self._failures[key] = bucket
            bucket.append(now)

    def reset(self, key: str) -> None:
        """清零该 key 的失败计数（通常在成功时调用）。"""
        with self._lock:
            self._failures.pop(key, None)

    def _prune(self, key: str, now: float) -> deque[float] | None:
        """清理该 key 窗口外的失败记录。

        返回有效的失败时间戳队列；key 无记录或全部过期时返回 None
        （同时删除 dict 中的空条目，避免无界增长）。
        """
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
