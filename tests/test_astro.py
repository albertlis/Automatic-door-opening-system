# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
from astro import (
    _last_sunday,
    civil_from_days,
    day_of_year,
    days_from_civil,
    days_in_month,
    is_dst,
    local_minutes,
    next_day,
    sun_times_cet,
)

# Tarnów
LAT = 50.01
LON = 20.99

# Reference: full NOAA spreadsheet algorithm (Julian century, iterated event time),
# throwaway script in scratchpad ref_noaa.py. Values rounded, CET (UTC+60, no DST).
REF = {
    (2024, 3, 20): (338, 1070),  # spring equinox
    (2024, 6, 21): (207, 1189),  # summer solstice
    (2024, 9, 22): (323, 1053),  # autumn equinox
    (2024, 12, 21): (452, 936),  # winter solstice
}
TOL_MIN = 3


# --- sun_times_cet vs reference (±3 min) ---


def test_sun_times_summer_solstice():
    rise, sset = sun_times_cet(2024, 6, 21, LAT, LON)
    assert abs(rise - 207) <= TOL_MIN
    assert abs(sset - 1189) <= TOL_MIN


def test_sun_times_winter_solstice():
    rise, sset = sun_times_cet(2024, 12, 21, LAT, LON)
    assert abs(rise - 452) <= TOL_MIN
    assert abs(sset - 936) <= TOL_MIN


def test_sun_times_spring_equinox():
    rise, sset = sun_times_cet(2024, 3, 20, LAT, LON)
    assert abs(rise - 338) <= TOL_MIN
    assert abs(sset - 1070) <= TOL_MIN


def test_sun_times_autumn_equinox():
    rise, sset = sun_times_cet(2024, 9, 22, LAT, LON)
    assert abs(rise - 323) <= TOL_MIN
    assert abs(sset - 1053) <= TOL_MIN


def test_sun_times_all_reference_dates():
    for (y, m, d), (rise_ref, set_ref) in REF.items():
        rise, sset = sun_times_cet(y, m, d, LAT, LON)
        assert abs(rise - rise_ref) <= TOL_MIN, (y, m, d)
        assert abs(sset - set_ref) <= TOL_MIN, (y, m, d)


def test_sun_times_range():
    # invariant: 0 < rise < set < 1440 for Tarnów on any reference date
    for y, m, d in REF:
        rise, sset = sun_times_cet(y, m, d, LAT, LON)
        assert 0 < rise < sset < 1440


def test_sun_times_polar_clamp_returns_ints():
    # 80°N midsummer: sun never sets -> cos(ha) clamped, no exception
    rise, sset = sun_times_cet(2024, 6, 21, 80.0, LON)
    assert isinstance(rise, int) and isinstance(sset, int)


# --- civil dusk (alt=-6) vs sunset (alt=-0.833): 30..60 min later ---


def test_civil_dusk_after_sunset_solstices_equinoxes():
    for y, m, d in REF:
        _, sset = sun_times_cet(y, m, d, LAT, LON)
        _, dusk = sun_times_cet(y, m, d, LAT, LON, alt_deg=-6)
        assert 30 <= dusk - sset <= 60, (y, m, d, dusk - sset)


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


def test_doy_year_2100_not_leap():
    # 2100 divisible by 100 but not 400 → not a leap year
    assert day_of_year(2100, 3, 1) == 60


def test_doy_year_2000_is_leap():
    # 2000 divisible by 400 → leap year
    assert day_of_year(2000, 3, 1) == 61


# --- days_in_month ---


def test_days_in_month_feb_leap():
    assert days_in_month(2024, 2) == 29


def test_days_in_month_feb_nonleap():
    assert days_in_month(2023, 2) == 28


def test_days_in_month_feb_2100_nonleap():
    assert days_in_month(2100, 2) == 28


def test_days_in_month_feb_2000_leap():
    assert days_in_month(2000, 2) == 29


def test_days_in_month_30_and_31():
    assert days_in_month(2024, 4) == 30
    assert days_in_month(2024, 12) == 31


# --- next_day ---


def test_next_day_within_month():
    assert next_day(2024, 6, 14) == (2024, 6, 15)


def test_next_day_month_end_31():
    assert next_day(2024, 1, 31) == (2024, 2, 1)


def test_next_day_month_end_30():
    assert next_day(2024, 6, 30) == (2024, 7, 1)


def test_next_day_feb_leap():
    assert next_day(2024, 2, 28) == (2024, 2, 29)
    assert next_day(2024, 2, 29) == (2024, 3, 1)


def test_next_day_feb_nonleap():
    assert next_day(2023, 2, 28) == (2023, 3, 1)


def test_next_day_year_end():
    assert next_day(2024, 12, 31) == (2025, 1, 1)


# --- _last_sunday ---


def test_last_sunday_march_2024():
    assert _last_sunday(2024, 3) == 31


def test_last_sunday_october_2024():
    assert _last_sunday(2024, 10) == 27


def test_last_sunday_march_2025():
    # 2025-03-31 = Monday → last Sunday = 30
    assert _last_sunday(2025, 3) == 30


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


# --- days_from_civil / civil_from_days (Hinnant, pure int) ---


def test_days_from_civil_epoch_is_zero():
    assert days_from_civil(1970, 1, 1) == 0


def test_days_from_civil_leap_day_2024():
    # 2024-01-01 = 19723 days (54 years, 13 leap days), + 31 (Jan) + 28 (Feb 1..28) = 19782
    assert days_from_civil(2024, 2, 29) == 19782


def test_civil_days_roundtrip():
    for z in range(-40000, 40000, 3):
        assert days_from_civil(*civil_from_days(z)) == z
    for y, m, d in [(1600, 2, 29), (1969, 12, 31), (2000, 2, 29), (2024, 2, 29), (2100, 3, 1)]:
        assert civil_from_days(days_from_civil(y, m, d)) == (y, m, d)
