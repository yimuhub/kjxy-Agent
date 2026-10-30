"""天气预报通道（v3.28）：和风天气 / OpenWeather，按小院配置。

诚实边界：

- **未配置就不编**：``provider=none`` 时返回 ``None`` 并给出「只用设备实况」的提示，
  规则不会凭空产生「未来降雨/降温」结论；
- **密钥不外泄**：出参只保留是否已配置，错误信息不含 key；
- **进程内缓存 30 分钟**：预报按天更新，短时重复运行不重复打供应商接口；
  多实例部署各自缓存（与推送限频同一诚实边界）。
"""

from __future__ import annotations

import hashlib
import threading
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)

CACHE_TTL_SECONDS = 1800
HTTP_TIMEOUT_SECONDS = 8

QWEATHER_24H = "https://devapi.qweather.com/v7/weather/24h"
QWEATHER_48H = "https://devapi.qweather.com/v7/weather/48h"
OPENWEATHER_FORECAST = "https://api.openweathermap.org/data/2.5/forecast"

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _cache_key(yard_id: str, provider: str, location: str, api_key: str) -> str:
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:8] if api_key else "nokey"
    return f"{yard_id}:{provider}:{location}:{digest}"


def clear_cache(yard_id: str | None = None) -> None:
    with _lock:
        if yard_id is None:
            _cache.clear()
            return
        for key in [k for k in _cache if k.startswith(f"{yard_id}:")]:
            _cache.pop(key, None)


def _get_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    """单次 GET；抽成函数便于测试替换，不把网络细节散落到规则里。"""
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("天气接口返回不是 JSON 对象")
    return data


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_qweather(
    hourly_24h: dict[str, Any], hourly_48h: dict[str, Any] | None = None
) -> dict[str, Any]:
    """和风天气 24h/48h 小时预报 → 统一口径（风速 km/h 转 m/s）。"""
    rows24 = hourly_24h.get("hourly") or []
    rows48 = (hourly_48h or {}).get("hourly") or rows24
    rain = sum(_num(row.get("precip")) or 0.0 for row in rows24)
    winds = [(_num(row.get("windSpeed")) or 0.0) / 3.6 for row in rows24]
    temps24 = [_num(row.get("temp")) for row in rows24]
    temps48 = [_num(row.get("temp")) for row in rows48]
    humid = [_num(row.get("humidity")) for row in rows24]
    temps_24h = [t for t in temps24 if t is not None]
    temps_48h = [t for t in temps48 if t is not None]
    humidities = [h for h in humid if h is not None]
    return {
        "provider": "qweather",
        "generated_at": datetime.now(UTC).isoformat(),
        "next_24h": {
            "rain_mm": round(rain, 2),
            "wind_max_ms": round(max(winds), 2) if winds else None,
            "temp_max": max(temps_24h) if temps_24h else None,
            "temp_min": min(temps_24h) if temps_24h else None,
            "humidity_mean": round(sum(humidities) / len(humidities), 1) if humidities else None,
        },
        "next_48h": {
            "rain_mm": None,
            "temp_min": min(temps_48h) if temps_48h else None,
        },
    }


def normalize_openweather(payload: dict[str, Any]) -> dict[str, Any]:
    """OpenWeather 3 小时预报 → 统一口径；只取未来 24h 与 48h。"""
    rows = payload.get("list") or []
    now = datetime.now(UTC)
    rows24: list[dict[str, Any]] = []
    rows48: list[dict[str, Any]] = []
    for row in rows:
        stamp = _num(row.get("dt"))
        if stamp is None:
            continue
        at = datetime.fromtimestamp(stamp, UTC)
        hours = (at - now).total_seconds() / 3600
        if hours < -1:
            continue
        if hours <= 24:
            rows24.append(row)
        if hours <= 48:
            rows48.append(row)
    rain = sum(_num((row.get("rain") or {}).get("3h")) or 0.0 for row in rows24)
    winds = [_num((row.get("wind") or {}).get("speed")) or 0.0 for row in rows24]
    temps24 = [_num((row.get("main") or {}).get("temp")) for row in rows24]
    temps48 = [_num((row.get("main") or {}).get("temp")) for row in rows48]
    humid = [_num((row.get("main") or {}).get("humidity")) for row in rows24]
    temps_24h = [t for t in temps24 if t is not None]
    temps_48h = [t for t in temps48 if t is not None]
    humidities = [h for h in humid if h is not None]
    return {
        "provider": "openweather",
        "generated_at": now.isoformat(),
        "next_24h": {
            "rain_mm": round(rain, 2),
            "wind_max_ms": round(max(winds), 2) if winds else None,
            "temp_max": max(temps_24h) if temps_24h else None,
            "temp_min": min(temps_24h) if temps_24h else None,
            "humidity_mean": round(sum(humidities) / len(humidities), 1) if humidities else None,
        },
        "next_48h": {
            "rain_mm": None,
            "temp_min": min(temps_48h) if temps_48h else None,
        },
    }


def _fetch_qweather(location: str, api_key: str) -> dict[str, Any]:
    hourly24 = _get_json(QWEATHER_24H, {"location": location, "key": api_key})
    hourly48 = _get_json(QWEATHER_48H, {"location": location, "key": api_key})
    return normalize_qweather(hourly24, hourly48)


def _fetch_openweather(setting: dict[str, Any], api_key: str) -> dict[str, Any]:
    lat = setting.get("weather_lat")
    lon = setting.get("weather_lon")
    params: dict[str, Any] = {"appid": api_key, "units": "metric", "lang": "zh_cn"}
    if isinstance(lat, int | float) and isinstance(lon, int | float):
        params.update({"lat": lat, "lon": lon})
    else:
        location = str(setting.get("weather_location") or "").strip()
        if not location:
            raise ValueError("OpenWeather 需要填写城市名或经纬度")
        params["q"] = location
    return normalize_openweather(_get_json(OPENWEATHER_FORECAST, params))


def forecast(
    yard_id: str, setting: dict[str, Any], *, use_cache: bool = True
) -> tuple[dict[str, Any] | None, list[str]]:
    """取未来 24/48 小时预报；返回 (预报, 提示)。失败不抛异常。"""
    provider = str(setting.get("weather_provider") or "none")
    if provider == "none":
        return None, ["未配置天气预报，本轮只用已接入设备的实况与趋势判断。"]
    api_key = str(setting.get("weather_api_key") or "").strip()
    location = str(setting.get("weather_location") or "").strip()
    if not api_key:
        return None, ["天气预报供应商未填写 API Key，本轮只用设备实况。"]
    if provider == "qweather" and not location:
        return None, ["和风天气需要填写 Location ID 或城市名，本轮只用设备实况。"]
    if provider not in {"qweather", "openweather"}:
        return None, [f"未知天气预报供应商「{provider}」，本轮只用设备实况。"]

    key = _cache_key(yard_id, provider, location, api_key)
    if use_cache:
        with _lock:
            hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_TTL_SECONDS:
            return hit[1], []

    try:
        data = (
            _fetch_qweather(location, api_key)
            if provider == "qweather"
            else _fetch_openweather(setting, api_key)
        )
    except Exception as e:  # noqa: BLE001 —— 天气失败不影响规则主流程
        # 只记异常类型与状态码，绝不把含 key 的 URL 写进日志
        status = getattr(getattr(e, "response", None), "status_code", None)
        log.warning(
            "weather forecast failed provider=%s status=%s err=%s",
            provider,
            status,
            type(e).__name__,
        )
        return None, [f"天气预报获取失败（{provider}），本轮只用设备实况与台账证据。"]

    with _lock:
        _cache[key] = (time.monotonic(), data)
    return data, []


__all__ = [
    "CACHE_TTL_SECONDS",
    "clear_cache",
    "forecast",
    "normalize_openweather",
    "normalize_qweather",
]
