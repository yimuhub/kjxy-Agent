#!/usr/bin/env python3
"""模拟设备源：给 webhook 设备造 24h 正弦波温湿度 + 随机墒情，批量 POST ingest。

纯 stdlib（urllib），无平台依赖，测试与联调均可直接使用。

用法：
  1) 已有设备（管理页/接口拿到的 device_id + 一次性 token）：
     python scripts/sim_devices.py --device-id <id> --token <token>
  2) 一步到位（自动用管理员令牌建 webhook 设备并打印新 token）：
     ADMIN_TOKEN=*** python scripts/sim_devices.py --create
     （ADMIN_TOKEN 为平台登录态 Bearer 令牌，也可用 --admin-token 传入）

限速说明：服务端每台设备 60 秒窗口最多 100 个读数点（超限 429）。本脚本默认
按 90 点/分钟节流回放（--points-per-min 可调）；只想"立刻灌一批"的冒烟场景
把 --hours 调小或直接接受 429 即可。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

DEFAULT_BASE = "http://127.0.0.1:8000/api/v1"


def _post(url: str, body: dict, headers: dict[str, str], timeout: float = 10.0) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise SystemExit(f"HTTP {e.code} {url}\n{detail}") from e
    except urllib.error.URLError as e:
        raise SystemExit(f"无法连接 {url}: {e}") from e


def create_device(base: str, admin_token: str) -> tuple[str, str]:
    name = f"模拟气象墒情站-{int(time.time()) % 100000}"
    body = {
        "name": name,
        "device_type": "weather",
        "source_type": "webhook",
        "model_hint": "sim-devices.py",
        "location_note": "脚本模拟设备（无实体）",
    }
    resp = _post(f"{base}/devices", body, {"Authorization": f"Bearer {admin_token}"})
    data = resp["data"]
    token = data.get("device_token")
    if not token:
        raise SystemExit("响应缺少 device_token（应为 webhook 设备一次性令牌）")
    print(f"已创建设备 {name}: id={data['id']}")
    print(f"一次性接入令牌（请立刻保存，接口不再回显）：\n  {token}")
    return data["id"], token


def gen_points(hours: int, step_min: int, seed: int) -> list[dict]:
    """正弦昼夜温湿度 + 墒情随机游走 + 偶发虫情计数。"""
    rng = random.Random(seed)
    now = datetime.now(UTC)
    soil = {10: 28.0, 20: 31.0, 40: 34.0, 60: 36.0}
    out: list[dict] = []
    n = int(hours * 60 / step_min)
    for i in range(n, 0, -1):
        ts = now - timedelta(minutes=step_min * i)
        hour_angle = (ts.hour + ts.minute / 60) / 24 * 2 * math.pi
        temp = round(18 + 10 * math.sin(hour_angle - math.pi / 2) + rng.uniform(-0.4, 0.4), 2)
        humidity = round(70 - 20 * math.sin(hour_angle - math.pi / 2) + rng.uniform(-3, 3), 1)
        for depth in soil:
            soil[depth] = max(
                5.0,
                min(60.0, soil[depth] + rng.uniform(-0.3, 0.5 if hour_angle > math.pi else 0.1)),
            )
        data: dict[str, object] = {
            "temp": temp,
            "humidity": humidity,
            "soil_moisture_10": round(soil[10], 1),
            "soil_moisture_20": round(soil[20], 1),
            "soil_moisture_40": round(soil[40], 1),
            "soil_moisture_60": round(soil[60], 1),
        }
        if rng.random() < 0.08:  # 少量字典外字段，验证 other + raw 保留
            data["vendor_status"] = f"OK-{rng.randint(0, 9)}"
        if rng.random() < 0.05:  # 虫情灯偶发计数（可能触发 gt 规则）
            data["pest_count"] = rng.randint(1, 120)
        out.append({"reported_at": ts.isoformat(), "data": data})
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="模拟设备：造 24h 数据回放 ingest")
    p.add_argument("--base-url", default=DEFAULT_BASE)
    p.add_argument("--device-id", default=os.environ.get("DEVICE_ID", ""))
    p.add_argument("--token", default=os.environ.get("DEVICE_TOKEN", ""))
    p.add_argument("--create", action="store_true", help="先用管理员令牌创建 webhook 设备")
    p.add_argument("--admin-token", default=os.environ.get("ADMIN_TOKEN", ""))
    p.add_argument("--hours", type=float, default=24.0)
    p.add_argument("--step-min", type=int, default=30, help="数据点间隔（分钟）")
    p.add_argument(
        "--points-per-min", type=float, default=90.0, help="客户端节流（对齐服务端 100/60s 限频）"
    )
    p.add_argument("--seed", type=int, default=20261019)
    args = p.parse_args()

    device_id, token = args.device_id, args.token
    if args.create:
        if not args.admin_token:
            raise SystemExit("--create 需要 --admin-token 或环境变量 ADMIN_TOKEN")
        device_id, token = create_device(args.base_url, args.admin_token)
    if not device_id or not token:
        raise SystemExit("需要 --device-id 与 --token（或使用 --create）")

    batches = gen_points(args.hours, args.step_min, args.seed)
    url = f"{args.base_url}/devices/{device_id}/ingest"
    points_total = sum(len(b["data"]) for b in batches)
    print(f"回放 {len(batches)} 批 / {points_total} 点 → {url}")
    sent = 0
    t0 = time.monotonic()
    for b in batches:
        _post(url, {"token": token, "reported_at": b["reported_at"], "data": b["data"]}, {})
        sent += len(b["data"])
        # 按点节流：保证任意 60s 窗口不超服务端限频（留 10% 余量）
        wait = sent / max(args.points_per_min, 1) * 60 - (time.monotonic() - t0)
        if wait > 0:
            time.sleep(wait)
    print(f"完成：{sent} 点全部 200。可用 GET /devices/{device_id}/readings 校验。")


if __name__ == "__main__":
    sys.exit(main())
