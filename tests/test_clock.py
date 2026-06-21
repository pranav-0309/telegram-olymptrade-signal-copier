from __future__ import annotations

import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from signal_copier.infra.clock import (
    format_local_hhmm,
    hhmm_to_unix,
    is_within_window,
    monotonic,
    now_unix,
    signal_date_in_tz,
)

# --- hhmm_to_unix ----------------------------------------------------------


def test_hhmm_to_unix_happy_path() -> None:
    # America/Sao_Paulo is UTC-3 (DST-free since 2019).
    tz = ZoneInfo("America/Sao_Paulo")
    # 10:20 on 2026-06-20 in UTC-3 is 13:20 UTC.
    # 2026-06-20 00:00:00 UTC = 1781913600; +13h20m = 1781961600.
    assert hhmm_to_unix("10:20", date(2026, 6, 20), tz) == pytest.approx(1781961600.0, abs=0.001)


def test_hhmm_to_unix_invalid_format_raises() -> None:
    tz = ZoneInfo("America/Sao_Paulo")
    with pytest.raises(ValueError):
        hhmm_to_unix("25:00", date(2026, 6, 20), tz)
    with pytest.raises(ValueError):
        hhmm_to_unix("10:99", date(2026, 6, 20), tz)
    with pytest.raises(ValueError):
        hhmm_to_unix("abc", date(2026, 6, 20), tz)
    with pytest.raises(ValueError):
        hhmm_to_unix("", date(2026, 6, 20), tz)


def test_hhmm_to_unix_at_midnight() -> None:
    tz = ZoneInfo("America/Sao_Paulo")
    # 00:00 on 2026-06-20 in UTC-3 is 03:00 UTC = 1781924400.
    assert hhmm_to_unix("00:00", date(2026, 6, 20), tz) == pytest.approx(1781924400.0, abs=0.001)
    # 23:59 on 2026-06-20 in UTC-3 is 02:59 UTC next day = 1782010740.
    assert hhmm_to_unix("23:59", date(2026, 6, 20), tz) == pytest.approx(1782010740.0, abs=0.001)


def test_hhmm_to_unix_across_dst_spring_forward() -> None:
    # America/New_York: DST starts 2026-03-08 02:00 -> 03:00.
    # At 02:30 on that day, local time doesn't exist; zoneinfo
    # resolves to 03:30 (one hour later).
    tz = ZoneInfo("America/New_York")
    skipped = hhmm_to_unix("02:30", date(2026, 3, 8), tz)
    expected_03_30 = hhmm_to_unix("03:30", date(2026, 3, 8), tz)
    assert skipped == pytest.approx(expected_03_30, abs=0.001)


def test_hhmm_to_unix_across_date_line() -> None:
    # Asia/Tokyo is UTC+9 (no DST). 01:00 Tokyo on date X is
    # 16:00 UTC on date X-1. 2026-06-19 16:00 UTC = 1781884800.
    tz = ZoneInfo("Asia/Tokyo")
    epoch = hhmm_to_unix("01:00", date(2026, 6, 20), tz)
    assert epoch == pytest.approx(1781884800.0, abs=0.001)


# --- signal_date_in_tz -----------------------------------------------------


def test_signal_date_in_tz_at_local_midnight() -> None:
    # 2026-06-20 00:00:00 in America/Sao_Paulo = 2026-06-20 03:00:00 UTC.
    # Epoch for that UTC instant: compute via the helper itself.
    tz = ZoneInfo("America/Sao_Paulo")
    midnight_local = hhmm_to_unix("00:00", date(2026, 6, 20), tz)
    assert signal_date_in_tz(midnight_local, tz) == date(2026, 6, 20)


def test_signal_date_in_tz_just_before_local_midnight() -> None:
    # 2026-06-19 23:59:59 in Sao_Paulo is still date 2026-06-19 locally.
    tz = ZoneInfo("America/Sao_Paulo")
    # 23:59 local = 02:59 UTC next day.
    utc_just_after_midnight = hhmm_to_unix("23:59", date(2026, 6, 19), tz) + 1
    assert signal_date_in_tz(utc_just_after_midnight, tz) == date(2026, 6, 19)


# --- is_within_window -----------------------------------------------------


def test_is_within_window_past_boundary() -> None:
    # Exactly 60s in the past is acceptable; 61s is not.
    now = 1_000.0
    assert is_within_window(now - 60.0, now) is True
    assert is_within_window(now - 61.0, now) is False


def test_is_within_window_future_boundary() -> None:
    now = 1_000.0
    assert is_within_window(now + 1_800.0, now) is True
    assert is_within_window(now + 1_801.0, now) is False


def test_is_within_window_default_tolerances() -> None:
    # Without explicit kwargs, defaults are 60s past / 1800s future.
    now = 1_000.0
    assert is_within_window(now - 30.0, now) is True
    assert is_within_window(now + 100.0, now) is True


def test_is_within_window_custom_tolerances() -> None:
    now = 1_000.0
    # Tighten the past tolerance to 10s.
    assert is_within_window(now - 5.0, now, past_tolerance=10.0) is True
    assert is_within_window(now - 11.0, now, past_tolerance=10.0) is False


# --- now_unix and monotonic ------------------------------------------------


def test_now_unix_close_to_time_time() -> None:
    assert now_unix() == pytest.approx(time.time(), abs=1.0)


def test_now_unix_returns_non_decreasing_values() -> None:
    a = now_unix()
    b = now_unix()
    assert b >= a


def test_monotonic_returns_non_decreasing_values() -> None:
    a = monotonic()
    b = monotonic()
    assert b >= a


# --- format_local_hhmm -----------------------------------------------------


def test_format_local_hhmm_america_sao_paulo() -> None:
    """2026-06-21T13:20:00Z is 10:20 in America/Sao_Paulo (UTC-3, no DST)."""
    tz = ZoneInfo("America/Sao_Paulo")
    # 13:20 UTC == 10:20 BRT
    unix_ts = datetime(2026, 6, 21, 13, 20, 0, tzinfo=ZoneInfo("UTC")).timestamp()
    assert format_local_hhmm(unix_ts, tz) == "10:20"


def test_format_local_hhmm_midnight_rollover() -> None:
    """Just past midnight in UTC-3 — verify the helper doesn't blow up."""
    tz = ZoneInfo("America/Sao_Paulo")
    unix_ts = datetime(2026, 6, 21, 3, 5, 0, tzinfo=ZoneInfo("UTC")).timestamp()  # 00:05 BRT
    assert format_local_hhmm(unix_ts, tz) == "00:05"
