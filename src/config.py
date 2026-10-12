# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
import json
import os

from astro import is_dst, sun_times_cet

DEFAULTS = {
    "location": {"lat": 50.01, "lon": 20.99},  # Tarnow
    "schedule": {
        "open_after_sunrise_min": 30,
        "close_after_dusk_min": 0,
        "open_not_before_min": None,  # local minutes 0..1439, None = off
        "close_not_after_min": None,  # local minutes 0..1439, None = off
    },
    "safety": {"move_timeout_s": 21},
    "wifi": {"ap_password": "", "webrepl_password": ""},  # ap "" = open AP (R6); webrepl "" = WebREPL off
}

_ALT_RISE = -0.833
_ALT_DUSK = -6
_DST_MIN = 60
_OFFSET = (-120, 180)
_TIMEOUT = (5, 120)
_replace = getattr(os, "replace", os.rename)


class ConfigError(Exception):
    pass


def _copy(obj):
    if isinstance(obj, dict):
        return {k: _copy(v) for k, v in obj.items()}
    return obj


def default_config() -> dict:
    return _copy(DEFAULTS)


def _merge(base: dict, over) -> dict:
    if not isinstance(over, dict):
        raise ConfigError("section is not an object")
    out = {}
    for k, v in base.items():
        if k not in over:
            out[k] = _copy(v)
        elif isinstance(v, dict):
            out[k] = _merge(v, over[k])
        else:
            out[k] = over[k]
    return out


def _check_keys(name: str, d, ref: dict) -> None:
    if not isinstance(d, dict):
        raise ConfigError(name + ": not an object")
    for k in ref:
        if k not in d:
            raise ConfigError(name + ": missing key " + k)
    for k in d:
        if k not in ref:
            raise ConfigError(name + ": unknown key " + str(k))


def _check_num(name: str, v, lo, hi, want_int: bool) -> None:
    t = type(v)  # type(), not isinstance: bool must be rejected
    if not (t is int or (t is float and not want_int)):
        raise ConfigError(name + ": wrong type")
    if not lo <= v <= hi:
        raise ConfigError(name + ": " + str(v) + " outside " + str(lo) + ".." + str(hi))


def validate_config(cfg) -> None:
    _check_keys("config", cfg, DEFAULTS)
    for section, ref in DEFAULTS.items():
        _check_keys(section, cfg[section], ref)
    loc = cfg["location"]
    _check_num("location.lat", loc["lat"], -90, 90, False)
    _check_num("location.lon", loc["lon"], -180, 180, False)
    sch = cfg["schedule"]
    _check_num("schedule.open_after_sunrise_min", sch["open_after_sunrise_min"], *_OFFSET, True)
    _check_num("schedule.close_after_dusk_min", sch["close_after_dusk_min"], *_OFFSET, True)
    for key in ("open_not_before_min", "close_not_after_min"):
        if sch[key] is not None:
            _check_num("schedule." + key, sch[key], 0, 1439, True)
    _check_num("safety.move_timeout_s", cfg["safety"]["move_timeout_s"], *_TIMEOUT, True)
    pw = cfg["wifi"]["ap_password"]
    # WPA2 passphrase = 8..63 printable ASCII (len in bytes == chars); non-ASCII would lock out the AP
    if type(pw) is not str or not (pw == "" or (8 <= len(pw) <= 63 and all(" " <= c <= "~" for c in pw))):
        raise ConfigError('wifi.ap_password: must be "" or 8..63 printable ASCII chars')
    # WebREPL: C side keeps a 10-byte buffer (9 chars max, ValueError above); "" = off
    wr = cfg["wifi"]["webrepl_password"]
    if type(wr) is not str or not (wr == "" or (4 <= len(wr) <= 9 and all(" " <= c <= "~" for c in wr))):
        raise ConfigError('wifi.webrepl_password: must be "" or 4..9 printable ASCII chars')


def load(path: str) -> dict:
    try:
        with open(path) as f:
            cfg = _merge(DEFAULTS, json.load(f))
        validate_config(cfg)
        return cfg
    except Exception as e:  # any failure -> defaults, never raise
        print("config: using defaults:", e)
        return default_config()


def save(cfg: dict, path: str) -> None:
    validate_config(cfg)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f)
    _replace(tmp, path)  # atomic; os.rename fails on Windows if target exists


def schedule(cfg: dict, y: int, m: int, d: int) -> tuple:
    loc = cfg["location"]
    lat, lon = loc["lat"], loc["lon"]
    rise, _ = sun_times_cet(y, m, d, lat, lon, alt_deg=_ALT_RISE)
    _, dusk = sun_times_cet(y, m, d, lat, lon, alt_deg=_ALT_DUSK)
    dst = _DST_MIN if is_dst(y, m, d, 12) else 0
    sch = cfg["schedule"]
    open_min = rise + dst + sch["open_after_sunrise_min"]
    close_min = dusk + dst + sch["close_after_dusk_min"]
    # clamps after DST + offsets: they are local-time bounds
    if sch["open_not_before_min"] is not None:
        open_min = max(open_min, sch["open_not_before_min"])
    if sch["close_not_after_min"] is not None:
        close_min = min(close_min, sch["close_not_after_min"])
    return open_min, close_min
