"""业务日切与全局 UTC 时间工具：库里时间戳一律 UTC，但「今天/本周/本月/当日上限」按业务时区切。

产品面向国内小院：按 UTC 0 点日切会让北京时间 0–8 点的日报告仍写「昨天」，
指挥官每日创建上限也要到早上 8 点才清零。所有按日历切的窗口都从这里取，
避免出现一半按 UTC、一半按本地的第二套偏差。
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: 业务时区名；桌面版或海外部署可用 TZ_BUSINESS 覆盖（如 TZ_BUSINESS=UTC）
BUSINESS_TZ_NAME = os.getenv("TZ_BUSINESS", "Asia/Shanghai")

#: 取不到时区数据库（精简容器、未打包 tzdata）时的兜底偏移，与产品主市场一致
_FALLBACK_OFFSET = timedelta(hours=8)


def business_tz() -> tzinfo:
    try:
        return ZoneInfo(BUSINESS_TZ_NAME)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone(_FALLBACK_OFFSET, name=BUSINESS_TZ_NAME)


def business_now() -> datetime:
    return datetime.now(business_tz())


def business_day_of(moment: datetime) -> date:
    """把任意瞬时归到业务时区的日历日（数据库里的时间戳都是 UTC 瞬时）。"""
    return moment.astimezone(business_tz()).date()


def business_today() -> date:
    return business_day_of(business_now())


def business_week_key() -> str:
    """业务时区下的 ISO 周键，如 2026-W45。"""
    iso = business_today().isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def month_key_of(moment: datetime) -> str:
    local = moment.astimezone(business_tz())
    return f"{local.year:04d}-{local.month:02d}"


def _local_midnight(day: date) -> datetime:
    """业务时区的当天 00:00，换算成 UTC 瞬时（数据库列都是 UTC 存）。"""
    return datetime.combine(day, time.min, tzinfo=business_tz()).astimezone(UTC)


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = _local_midnight(day)
    return start, _local_midnight(day + timedelta(days=1))


def week_bounds(week_start: date) -> tuple[datetime, datetime]:
    start = _local_midnight(week_start)
    return start, _local_midnight(week_start + timedelta(days=7))


def month_bounds(month_key: str) -> tuple[datetime, datetime]:
    year, month = (int(part) for part in month_key.split("-", 1))
    first = date(year, month, 1)
    following = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return _local_midnight(first), _local_midnight(following)


def utc_now() -> datetime:
    """返回带 UTC 时区的当前时间（统一替代各模块散落的 _utcnow / _now）。"""
    return datetime.now(UTC)


def utc_iso_now() -> str:
    """返回当前 UTC 时间的 ISO-8601 字符串。"""
    return datetime.now(UTC).isoformat()


def ensure_utc(moment: datetime | None) -> datetime | None:
    """将 naive datetime 补齐为 UTC，已是 aware 则保持/转换至 UTC（统一替代 _as_utc / _aware）。"""
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def ensure_utc_strict(moment: datetime) -> datetime:
    """非空版本的 UTC 补齐（用于类型要求非 None 的调用方）。"""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def to_iso(moment: Any) -> str | None:
    """将 datetime 统一格式化为带 UTC 时区的 ISO-8601 字符串（统一替代 _iso）。"""
    if moment is None:
        return None
    if isinstance(moment, datetime):
        aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
        return aware.isoformat()
    if hasattr(moment, "isoformat"):
        return str(moment.isoformat())
    return str(moment)


def to_iso_or_empty(moment: Any) -> str:
    """将 datetime 格式化为 ISO-8601 字符串，None 时返回空串 ""。"""
    res = to_iso(moment)
    return res if res is not None else ""
