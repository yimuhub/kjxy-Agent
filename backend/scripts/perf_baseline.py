"""性能基线测量（v3.71）：对运行中的 dev 环境打真实请求，记录关键路径耗时。

用法：cd backend && DATABASE_URL=... ../.venv/bin/python scripts/perf_baseline.py
输出每项 P50/P95 与结论摘要，人工粘贴进 docs/perf-baseline-*.md。
覆盖：登录、概览聚合、任务列表、工单列表、指挥官 overview、SSE 建流首帧。
不是压测工具——是单并发基线，用于发现「越用越慢」的回归。
"""

from __future__ import annotations

import sys
import time

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
N = 10


def timed(fn, n: int = N) -> list[float]:
    samples: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            print(f"  ! {type(e).__name__}: {e}")
            return []
        samples.append((time.perf_counter() - start) * 1000)
        time.sleep(0.15)
    return samples


def p(samples: list[float], ratio: float) -> float:
    if not samples:
        return float("nan")
    ordered = sorted(samples)
    idx = min(len(ordered) - 1, int(len(ordered) * ratio))
    return round(ordered[idx], 1)


def main() -> int:
    client = httpx.Client(timeout=30)
    login = client.post(
        f"{BASE}/auth/login", json={"username": "yard_admin", "password": "yard123"}
    )
    assert login.status_code == 200, login.text
    token = login.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    cases: list[tuple[str, object]] = [
        ("GET /meta", lambda: client.get(f"{BASE}/meta")),
        (
            "GET /yards/current/owner/overview",
            lambda: client.get(f"{BASE}/yards/current/owner/overview", headers=headers),
        ),
        (
            "GET /yards/current/owner/roi",
            lambda: client.get(f"{BASE}/yards/current/owner/roi", headers=headers),
        ),
        (
            "GET /tasks?status=running",
            lambda: client.get(f"{BASE}/tasks?status=running", headers=headers),
        ),
        ("GET /farm/work-orders", lambda: client.get(f"{BASE}/farm/work-orders", headers=headers)),
        (
            "GET /commander/overview",
            lambda: client.get(f"{BASE}/commander/overview", headers=headers),
        ),
        ("GET /chat/sessions", lambda: client.get(f"{BASE}/chat/sessions", headers=headers)),
    ]

    print(f"性能基线（n={N}，单并发，ms）—— {time.strftime('%Y-%m-%d %H:%M')}")
    print("| 接口 | P50 | P95 |")
    print("| --- | --- | --- |")
    bad = 0
    for name, fn in cases:
        samples = timed(fn)
        if not samples:
            print(f"| {name} | FAIL | FAIL |")
            bad += 1
            continue
        p50, p95 = p(samples, 0.5), p(samples, 0.95)
        flag = " ⚠️" if p95 > 1500 else ""
        print(f"| {name} | {p50} | {p95}{flag} |")
    client.close()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
