"""业务日切口径（C12：日报告、月上限与创建上限都按同一时区切，不再一半 UTC）。"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from app.core import timezone as business_time


def test_default_business_zone_is_beijing_time() -> None:
    offset = datetime(2026, 11, 3, tzinfo=business_time.business_tz()).utcoffset()
    assert offset == timedelta(hours=8)


def test_day_bounds_start_at_business_midnight() -> None:
    day = date(2026, 11, 3)
    start, end = business_time.day_bounds(day)
    assert start.astimezone(business_time.business_tz()).time() == time.min
    assert end.astimezone(business_time.business_tz()).date() == day + timedelta(days=1)
    assert start < end


def test_day_bounds_are_utc_instants() -> None:
    """库里时间戳是 UTC：窗口边界也必须带 UTC，比较才不会错位。"""
    start, _end = business_time.day_bounds(date(2026, 11, 3))
    assert start.tzinfo == UTC


def test_month_key_follows_business_boundary() -> None:
    """跨过业务月界的最后一秒仍属上月——这正是 UTC 口径下「月初 8 小时内算错月」的位置。"""
    start_of_november = business_time.month_bounds("2026-11")[0]
    assert business_time.month_key_of(start_of_november) == "2026-11"
    assert business_time.month_key_of(start_of_november - timedelta(seconds=1)) == "2026-10"


def test_week_bounds_cover_seven_business_days() -> None:
    monday = date(2026, 11, 2)
    start, end = business_time.week_bounds(monday)
    assert start.astimezone(business_time.business_tz()).date() == monday
    assert end - start == timedelta(days=7)


def test_business_week_key_matches_business_today() -> None:
    iso = business_time.business_today().isocalendar()
    assert business_time.business_week_key() == f"{iso.year}-W{iso.week:02d}"
