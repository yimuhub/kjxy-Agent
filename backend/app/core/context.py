"""请求上下文与进程内指标。

- request_id 通过 contextvar 贯穿请求生命周期，供日志 Filter 与业务代码读取；
- 指标计数器保存 HTTP 请求数/时延分布与 LLM 调用计数，由 /metrics 输出
  Prometheus 文本（§16.2 最低监控指标的最小实现，多进程部署时数据为各进程独立）。
"""

import threading
import time
from collections import defaultdict
from contextvars import ContextVar

# 当前请求的 request_id；中间件写入，日志 Filter 读取
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# HTTP 时延直方图桶（秒）；最后 +Inf 由代码补
_LATENCY_BUCKETS: tuple[float, ...] = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)


class Metrics:
    """线程安全的进程内计数器（单 uvicorn 进程场景足够；扩容前置项见运维手册）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.http_requests: dict[tuple[str, str, int], int] = defaultdict(int)
        self.http_latency: dict[str, list[int]] = {}
        self.http_latency_sum: dict[str, float] = {}
        self.http_latency_count: dict[str, int] = defaultdict(int)
        self.llm_calls: dict[tuple[str, str], int] = defaultdict(int)  # (provider_status) -> n
        self.alerts_sent: dict[str, int] = defaultdict(int)  # level -> n
        self.started_at = time.time()

    def observe_http(self, *, route: str, method: str, status: int, seconds: float) -> None:
        with self._lock:
            self.http_requests[(route, method, int(status))] += 1
            buckets = self.http_latency.get(route)
            if buckets is None:
                buckets = self.http_latency[route] = [0] * len(_LATENCY_BUCKETS)
                self.http_latency_sum[route] = 0.0
            for i, bound in enumerate(_LATENCY_BUCKETS):
                if seconds <= bound:
                    # 每个样本只计入第一个命中的桶，导出时再做前缀累加（审查 O5）：
                    # 两边都累积会让 le=0.05 也涨到样本数，破坏 Prometheus 语义。
                    buckets[i] += 1
                    break
            self.http_latency_sum[route] += seconds
            self.http_latency_count[route] += 1

    def observe_llm(self, *, status: str, provider: str = "unknown") -> None:
        with self._lock:
            self.llm_calls[(provider, status)] += 1

    def observe_alert(self, level: str) -> None:
        with self._lock:
            self.alerts_sent[level] += 1

    def exposition(self) -> str:
        """Prometheus text format 0.0.4。"""
        lines: list[str] = []
        lines.append("# HELP http_requests_total Total HTTP requests by route/method/status")
        lines.append("# TYPE http_requests_total counter")
        with self._lock:
            for (route, method, status), count in sorted(self.http_requests.items()):
                lines.append(
                    f'http_requests_total{{method="{method}",route="{route}",'
                    f'status="{status}"}} {count}'
                )
            lines.append("# HELP http_request_duration_seconds HTTP latency histogram")
            lines.append("# TYPE http_request_duration_seconds histogram")
            for route, buckets in sorted(self.http_latency.items()):
                cumulative = 0
                for i, bound in enumerate(_LATENCY_BUCKETS):
                    cumulative += buckets[i]
                    lines.append(
                        f'http_request_duration_seconds_bucket{{le="{bound}",route="{route}"}} '
                        f"{cumulative}"
                    )
                total = self.http_latency_count[route]
                lines.append(
                    f'http_request_duration_seconds_bucket{{le="+Inf",route="{route}"}} {total}'
                )
                lines.append(f'http_request_duration_seconds_count{{route="{route}"}} {total}')
                sum_val = self.http_latency_sum[route]
                lines.append(f'http_request_duration_seconds_sum{{route="{route}"}} {sum_val:.6f}')
            lines.append("# HELP llm_calls_total LLM calls by provider and status")
            lines.append("# TYPE llm_calls_total counter")
            for (provider, llm_status), count in sorted(self.llm_calls.items()):
                lines.append(
                    f'llm_calls_total{{provider="{provider}",status="{llm_status}"}} {count}'
                )
            lines.append("# HELP alerts_sent_total Operational alerts dispatched by level")
            lines.append("# TYPE alerts_sent_total counter")
            for level, count in sorted(self.alerts_sent.items()):
                lines.append(f'alerts_sent_total{{level="{level}"}} {count}')
            lines.append("# HELP process_start_time_seconds Process start (unix seconds)")
            lines.append("# TYPE process_start_time_seconds gauge")
            lines.append(f"process_start_time_seconds {int(self.started_at)}")
        return "\n".join(lines) + "\n"


metrics = Metrics()
