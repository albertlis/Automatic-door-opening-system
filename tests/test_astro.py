# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
from src.astro import (
    _last_sunday,
    day_of_year,
    is_dst,
    local_minutes,
    sun_times_cet,
)

# --- sun_times_cet (solstice verification §5.3) ---


def test_summer_solstice():
    rise, sset = sun_times_cet(2024, 6, 21)
    assert rise == 206
    assert sset == 1193


def test_winter_solstice():
    rise, sset = sun_times_cet(2024, 12, 21)
    assert rise == 453
    assert sset == 943


# --- day_of_year ---


def test_doy_jan1():
    assert day_of_year(2024, 1, 1) == 1


def test_doy_dec31_leap():
    assert day_of_year(2024, 12, 31) == 366


def test_doy_dec31_nonleap():
    assert day_of_year(2023, 12, 31) == 365


def test_doy_mar1_leap():
    assert day_of_year(2024, 3, 1) == 61


def test_doy_mar1_nonleap():
    assert day_of_year(2023, 3, 1) == 60


# --- _last_sunday ---


def test_last_sunday_march_2024():
    assert _last_sunday(2024, 3) == 31


def test_last_sunday_october_2024():
    assert _last_sunday(2024, 10) == 27


# --- is_dst (2024: start=Mar 31, end=Oct 27) ---


def test_dst_winter():
    assert is_dst(2024, 1, 15, 12) is False


def test_dst_before_start():
    assert is_dst(2024, 3, 31, 1) is False


def test_dst_at_start():
    assert is_dst(2024, 3, 31, 2) is True


def test_dst_summer():
    assert is_dst(2024, 7, 1, 12) is True


def test_dst_before_end():
    assert is_dst(2024, 10, 27, 1) is True


def test_dst_at_end():
    assert is_dst(2024, 10, 27, 2) is False


def test_dst_november():
    assert is_dst(2024, 11, 1, 12) is False


def test_dst_december():
    assert is_dst(2024, 12, 25, 12) is False


# --- local_minutes ---


def test_local_minutes_winter():
    # 2024-01-15 08:30 CET → 510, no DST
    assert local_minutes(2024, 1, 15, 8, 30) == 510


def test_local_minutes_summer():
    # 2024-07-01 08:30 CET → 510 + 60 (CEST) = 570
    assert local_minutes(2024, 7, 1, 8, 30) == 570


def test_local_minutes_midnight():
    # 2024-01-15 00:00 CET → 0
    assert local_minutes(2024, 1, 15, 0, 0) == 0


def test_local_minutes_overflow():
    # 2024-07-01 23:30 CET → 23*60+30+60 = 1470 (>1440, documented edge case)
    assert local_minutes(2024, 7, 1, 23, 30) == 1470


def test_local_minutes_midnight_cest():
    # CEST midnight: 00:00 CET in summer → +60 = 60
    assert local_minutes(2024, 7, 1, 0, 0) == 60


# --- is_dst: untested branches in transition months ---


def test_dst_march_before_last_sunday():
    # 2024-03-10, ls=31 → d < ls → d > ls = False
    assert is_dst(2024, 3, 10, 12) is False


def test_dst_march_after_last_sunday():
    # 2025-03-31, ls=30 (31 Mar is Monday) → d > ls → True
    assert is_dst(2025, 3, 31, 12) is True


def test_dst_october_before_last_sunday():
    # 2024-10-20, ls=27 → d < ls → True
    assert is_dst(2024, 10, 20, 12) is True


def test_dst_october_after_last_sunday():
    # 2024-10-28, ls=27 → d > ls → False
    assert is_dst(2024, 10, 28, 12) is False


# --- _last_sunday: only ls=31 was tested ---


def test_last_sunday_march_2025():
    # 2025-03-31 = Monday → last Sunday = 30
    assert _last_sunday(2025, 3) == 30


# --- _is_leap: Gregorian century rules ---


def test_doy_year_2100_not_leap():
    # 2100 divisible by 100 but not 400 → not a leap year
    assert day_of_year(2100, 3, 1) == 60


def test_doy_year_2000_is_leap():
    # 2000 divisible by 400 → leap year
    assert day_of_year(2000, 3, 1) == 61


# --- sun_times_cet: curve shape at equinox ---


def test_sun_times_equinox():
    # 2024-03-21 (spring equinox), pre-calculated: doy=81
    rise, sset = sun_times_cet(2024, 3, 21)
    assert rise == 329
    assert sset == 1069


def test_sun_times_range():
    # invariant: 0 < rise < set < 1440 for any date
    rise, sset = sun_times_cet(2024, 3, 21)
    assert 0 < rise < sset < 1440
