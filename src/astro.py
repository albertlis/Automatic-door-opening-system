# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
import math

_DAYS_IN_MONTH = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
_DEG = math.pi / 180


def _is_leap(y: int) -> bool:
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def days_in_month(y: int, m: int) -> int:
    if m == 2 and _is_leap(y):
        return 29
    return _DAYS_IN_MONTH[m]


def day_of_year(y: int, m: int, d: int) -> int:
    """Calendar day of year (1-based). Leap-year aware."""
    doy = d
    for i in range(1, m):
        doy += days_in_month(y, i)
    return doy


def next_day(y: int, m: int, d: int) -> tuple[int, int, int]:
    d += 1
    if d > days_in_month(y, m):
        d = 1
        m += 1
        if m > 12:
            m = 1
            y += 1
    return y, m, d


def sun_times_cet(y: int, m: int, d: int, lat: float, lon: float, alt_deg: float = -0.833) -> tuple[int, int]:
    """(rise_min, set_min) in CET (UTC+1), minutes since midnight, no DST.
    NOAA simplified algorithm (Spencer Fourier series). alt_deg: -0.833 sunrise/sunset,
    -6 civil twilight. Polar day/night: cos(ha) clamped to [-1, 1].
    """
    g = 2 * math.pi / 365 * (day_of_year(y, m, d) - 1)
    cg, sg = math.cos(g), math.sin(g)
    c2, s2 = math.cos(2 * g), math.sin(2 * g)
    c3, s3 = math.cos(3 * g), math.sin(3 * g)
    eqtime = 229.18 * (0.000075 + 0.001868 * cg - 0.032077 * sg - 0.014615 * c2 - 0.040849 * s2)
    decl = 0.006918 - 0.399912 * cg + 0.070257 * sg - 0.006758 * c2 + 0.000907 * s2 - 0.002697 * c3 + 0.00148 * s3
    lat_r = lat * _DEG
    # ponytail: max(1e-9) guards lat=±90 (cos(lat)=0); clamp below handles the rest
    den = max(math.cos(lat_r) * math.cos(decl), 1e-9)
    cos_ha = (math.sin(alt_deg * _DEG) - math.sin(lat_r) * math.sin(decl)) / den
    cos_ha = max(-1.0, min(1.0, cos_ha))
    ha = math.acos(cos_ha) / _DEG
    rise = round(720 - 4 * (lon + ha) - eqtime + 60)
    sset = round(720 - 4 * (lon - ha) - eqtime + 60)
    return rise, sset


def _day_of_week(y: int, m: int, d: int) -> int:
    """0=Sunday, 1=Monday, ..., 6=Saturday. Tomohiko Sakamoto algorithm."""
    t = [0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4]
    if m < 3:
        y -= 1
    return (y + y // 4 - y // 100 + y // 400 + t[m - 1] + d) % 7


def _last_sunday(y: int, m: int) -> int:
    """Day-of-month of the last Sunday in month m of year y."""
    dim = days_in_month(y, m)
    return dim - _day_of_week(y, m, dim)


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


def days_from_civil(y: int, m: int, d: int) -> int:
    """Days since 1970-01-01 (Howard Hinnant days_from_civil). Floor division handles y < 0."""
    if m <= 2:
        y -= 1
    era = y // 400
    yoe = y - era * 400
    doy = (153 * (m - 3 if m > 2 else m + 9) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def civil_from_days(z: int) -> tuple[int, int, int]:
    """(y, m, d) from days since 1970-01-01. Inverse of days_from_civil (Hinnant civil_from_days)."""
    z += 719468
    era = z // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + 3 if mp < 10 else mp - 9
    y = yoe + era * 400
    return (y + 1 if m <= 2 else y), m, d
