"""进程内指标：直方图桶只累积一次，导出符合 Prometheus 语义（审查 O5）。"""

from app.core.context import Metrics


def _buckets(text: str, route: str = "/chat") -> dict[str, float]:
    prefix = 'http_request_duration_seconds_bucket{le="'
    result: dict[str, float] = {}
    for line in text.splitlines():
        if not line.startswith(prefix) or f'route="{route}"' not in line:
            continue
        le = line.split('le="', 1)[1].split('"', 1)[0]
        result[le] = float(line.rsplit(" ", 1)[1])
    return result


def _summary_value(text: str, name: str, route: str = "/chat") -> float:
    for line in text.splitlines():
        if line.startswith(f'{name}{{route="{route}"}}'):
            return float(line.rsplit(" ", 1)[1])
    raise AssertionError(f"{name} missing")


def test_single_sample_only_reaches_matching_buckets():
    metrics = Metrics()
    metrics.observe_http(route="/chat", method="POST", status=200, seconds=0.04)
    text = metrics.exposition()
    buckets = _buckets(text)
    # 40ms 落在 le=0.05：所有 >=0.05 的桶累计值都应为 1，小于 0.05 的桶为 0
    assert buckets["0.05"] == 1
    assert buckets["0.1"] == 1
    assert buckets["30.0"] == 1
    assert buckets["+Inf"] == 1
    assert _summary_value(text, "http_request_duration_seconds_count") == 1
    assert abs(_summary_value(text, "http_request_duration_seconds_sum") - 0.04) < 1e-9


def test_histogram_accumulates_prefix_once():
    metrics = Metrics()
    metrics.observe_http(route="/chat", method="POST", status=200, seconds=0.04)
    metrics.observe_http(route="/chat", method="POST", status=200, seconds=0.2)
    metrics.observe_http(route="/chat", method="POST", status=200, seconds=40.0)
    buckets = _buckets(metrics.exposition())
    # 0.04 -> le=0.05；0.2 -> le=0.25；40s 超过最大有限桶，只进 +Inf
    assert buckets["0.05"] == 1
    assert buckets["0.1"] == 1
    assert buckets["0.25"] == 2
    assert buckets["0.5"] == 2
    assert buckets["30.0"] == 2
    assert buckets["+Inf"] == 3


def test_other_routes_are_isolated():
    metrics = Metrics()
    metrics.observe_http(route="/chat", method="POST", status=200, seconds=0.04)
    metrics.observe_http(route="/search", method="GET", status=200, seconds=0.3)
    text = metrics.exposition()
    assert _buckets(text, "/chat")["0.05"] == 1
    assert _buckets(text, "/search")["0.05"] == 0
    assert _buckets(text, "/search")["0.5"] == 1
