# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
import math

_RISE_MEAN = (453 + 206) / 2  # 329.5 min
_RISE_AMP = (453 - 206) / 2  # 123.5 min
_SET_MEAN = (943 + 1193) / 2  # 1068.0 min
_SET_AMP = (1193 - 943) / 2  # 125.0 min
_SUMMER_DOY = 172  # 21 June

_DAYS_BEFORE = [0, 0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
_DAYS_IN_MONTH = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def _is_leap(y: int) -> bool:
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def day_of_year(y: int, m: int, d: int) -> int:
    """Calendar day of year (1-based). Leap-year aware."""
    doy = _DAYS_BEFORE[m] + d
    if m > 2 and _is_leap(y):
        doy += 1
    return doy


def sun_times_cet(y: int, m: int, d: int) -> tuple[int, int]:
    """Return (sunrise_min, sunset_min) in CET (UTC+1), no DST.
    Cosine approximation for Tarnów ~50°N 21°E. Accuracy ~±15 min.
    Uses round() to match solstice anchor values (int() would truncate).
    """
    doy = day_of_year(y, m, d)
    angle = 2 * math.pi * (doy - _SUMMER_DOY) / 365
    rise = round(_RISE_MEAN - _RISE_AMP * math.cos(angle))
    sset = round(_SET_MEAN + _SET_AMP * math.cos(angle))
    return rise, sset


def _day_of_week(y: int, m: int, d: int) -> int:
    """0=Sunday, 1=Monday, ..., 6=Saturday. Tomohiko Sakamoto algorithm."""
    t = [0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4]
    if m < 3:
        y -= 1
    return (y + y // 4 - y // 100 + y // 400 + t[m - 1] + d) % 7


def _last_sunday(y: int, m: int) -> int:
    """Day-of-month of the last Sunday in month m of year y."""
    dim = _DAYS_IN_MONTH[m]
    if m == 2 and _is_leap(y):
        dim = 29
    dow = _day_of_week(y, m, dim)  # 0=Sunday
    return dim - dow


def is_dst(y: int, m: int, d: int, h: int) -> bool:
    """True when CEST (summer time) is active. DS3231 stores CET always.
    Start: last Sunday March at 02:00 CET.
    End:   last Sunday October at 02:00 CET.
    """
    if m < 3 or m > 10:
        return False
    if 3 < m < 10:
        return True
    ls = _last_sunday(y, m)
    if m == 3:
        if d != ls:
            return d > ls
        return h >= 2
    else:  # m == 10
        if d != ls:
            return d < ls
        return h < 2


def local_minutes(y: int, m: int, d: int, h: int, minute: int) -> int:
    """Minutes since midnight in local time (CET or CEST).
    May exceed 1440 during CEST at CET 23:xx — callers must handle.
    """
    return h * 60 + minute + (60 if is_dst(y, m, d, h) else 0)
