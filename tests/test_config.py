# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
import json
import os

import pytest

from astro import sun_times_cet
from config import (
    DEFAULTS,
    ConfigError,
    default_config,
    load,
    save,
    schedule,
    validate_config,
)

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DEFAULT_JSON = os.path.join(ROOT, "config.default.json")
SUMMER = (2026, 6, 15)  # DST active at 12:00
WINTER = (2026, 12, 15)  # CET


def _with(section, key, value):
    cfg = default_config()
    cfg[section][key] = value
    return cfg


def _write(tmp_path, name, data):
    p = tmp_path / name
    text = data if isinstance(data, str) else json.dumps(data)
    p.write_text(text, encoding="utf-8")
    return str(p)


# --- default_config / config.default.json ---


def test_default_config_equals_defaults():
    assert default_config() == DEFAULTS


def test_default_config_is_deep_copy():
    cfg = default_config()
    cfg["location"]["lat"] = 0.0
    cfg["safety"]["move_timeout_s"] = 99
    assert DEFAULTS["location"]["lat"] == 50.01
    assert DEFAULTS["safety"]["move_timeout_s"] == 21


def test_default_json_matches_defaults():
    with open(DEFAULT_JSON, encoding="utf-8") as f:
        assert json.load(f) == DEFAULTS
    assert load(DEFAULT_JSON) == DEFAULTS


# --- load robustness ---


@pytest.mark.parametrize(
    "pw,ok",
    [
        ("", True),
        ("abcd", True),
        ("a" * 9, True),
        ("abc", False),
        ("a" * 10, False),
        (None, False),
        (1234, False),
        ("ą" * 4, False),  # non-ASCII
        ("abc\x01d", False),  # control char
    ],
)
def test_validate_webrepl_password(pw, ok):
    cfg = default_config()
    cfg["wifi"]["webrepl_password"] = pw
    if ok:
        validate_config(cfg)
    else:
        with pytest.raises(ConfigError, match="webrepl_password"):
            validate_config(cfg)


def test_load_old_config_without_webrepl_key_gets_default(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"wifi": {"ap_password": "abcdefgh"}}))
    cfg = load(str(p))
    assert cfg["wifi"] == {"ap_password": "abcdefgh", "webrepl_password": ""}


def test_load_missing_file_returns_defaults(tmp_path, capsys):
    assert load(str(tmp_path / "nope.json")) == DEFAULTS
    assert "config" in capsys.readouterr().out


def test_load_corrupt_json_returns_defaults(tmp_path):
    assert load(_write(tmp_path, "bad.json", "{not json")) == DEFAULTS


def test_load_non_object_returns_defaults(tmp_path):
    assert load(_write(tmp_path, "list.json", [1, 2])) == DEFAULTS


def test_load_section_wrong_type_returns_defaults(tmp_path):
    assert load(_write(tmp_path, "s.json", {"location": 5})) == DEFAULTS


def test_load_partial_file_merges_over_defaults(tmp_path):
    cfg = load(_write(tmp_path, "p.json", {"location": {"lat": 52.23}}))
    assert cfg["location"] == {"lat": 52.23, "lon": 20.99}
    assert cfg["schedule"] == DEFAULTS["schedule"]
    assert cfg["safety"] == DEFAULTS["safety"]


def test_load_invalid_value_returns_full_defaults(tmp_path):
    data = {"location": {"lat": 52.23}, "safety": {"move_timeout_s": 500}}
    # whole file rejected, not partially merged
    assert load(_write(tmp_path, "inv.json", data)) == DEFAULTS


def test_load_ignores_unknown_top_level_key(tmp_path):
    data = {"future": {"x": 1}, "safety": {"move_timeout_s": 30}}
    cfg = load(_write(tmp_path, "u.json", data))
    assert cfg["safety"]["move_timeout_s"] == 30
    assert "future" not in cfg


# --- save ---


def test_save_load_roundtrip_no_tmp_left(tmp_path):
    cfg = default_config()
    cfg["location"]["lat"] = 51.5
    cfg["schedule"]["close_after_dusk_min"] = -15
    path = str(tmp_path / "config.json")
    save(cfg, path)
    assert load(path) == cfg
    assert not os.path.exists(path + ".tmp")


def test_save_overwrites_existing_file(tmp_path):
    path = str(tmp_path / "config.json")
    save(default_config(), path)
    cfg = _with("safety", "move_timeout_s", 30)
    save(cfg, path)
    assert load(path) == cfg
    assert not os.path.exists(path + ".tmp")


def test_save_invalid_writes_nothing(tmp_path):
    path = str(tmp_path / "config.json")
    with pytest.raises(ConfigError):
        save(_with("safety", "move_timeout_s", 1), path)
    assert not os.path.exists(path)
    assert not os.path.exists(path + ".tmp")


# --- validate_config: structure ---


def test_validate_defaults_passes():
    validate_config(default_config())


def test_validate_rejects_unknown_top_level_key():
    cfg = default_config()
    cfg["light"] = {"lux_open": 8.0}
    with pytest.raises(ConfigError):
        validate_config(cfg)


def test_validate_rejects_unknown_nested_key():
    cfg = default_config()
    cfg["schedule"]["sunrise_offset"] = 0
    with pytest.raises(ConfigError):
        validate_config(cfg)


def test_validate_rejects_missing_top_level_key():
    cfg = default_config()
    del cfg["safety"]
    with pytest.raises(ConfigError):
        validate_config(cfg)


def test_validate_rejects_missing_nested_key():
    cfg = default_config()
    del cfg["location"]["lon"]
    with pytest.raises(ConfigError):
        validate_config(cfg)


def test_validate_rejects_non_dict_section():
    cfg = default_config()
    cfg["safety"] = [21]
    with pytest.raises(ConfigError):
        validate_config(cfg)


# --- validate_config: types ---


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("safety", "move_timeout_s", True),
        ("schedule", "open_after_sunrise_min", True),
        ("schedule", "close_after_dusk_min", False),
        ("location", "lat", True),
        ("location", "lon", False),
    ],
)
def test_validate_rejects_bool(section, key, value):
    with pytest.raises(ConfigError):
        validate_config(_with(section, key, value))


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("safety", "move_timeout_s", 21.0),
        ("schedule", "open_after_sunrise_min", 30.5),
        ("schedule", "close_after_dusk_min", "0"),
        ("location", "lat", "50.0"),
        ("location", "lat", None),
    ],
)
def test_validate_rejects_wrong_type(section, key, value):
    with pytest.raises(ConfigError):
        validate_config(_with(section, key, value))


# --- validate_config: wifi.ap_password (R6) ---


@pytest.mark.parametrize(
    "pw,ok",
    [
        ("", True),
        ("x" * 8, True),
        ("x" * 63, True),
        ("x" * 7, False),
        ("x" * 64, False),
        (None, False),
        (12345678, False),
        ("A1!~" * 2, True),  # 8 printable ASCII
        ("A1!~" * 15 + "xyz", True),  # 63 printable ASCII
        ("ą" * 32, False),  # 32 chars, non-ASCII (WPA2 passphrase is ASCII only)
        ("x" * 7 + "\x01", False),  # 8 chars, control char
        ("x" * 7 + "\x7f", False),  # 8 chars, DEL is not printable
    ],
)
def test_validate_ap_password(pw, ok):
    cfg = default_config()
    cfg["wifi"]["ap_password"] = pw
    if ok:
        validate_config(cfg)
    else:
        with pytest.raises(ConfigError):
            validate_config(cfg)


# --- validate_config: ranges ---


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("location", "lat", 90.01),
        ("location", "lat", -90.01),
        ("location", "lon", 180.01),
        ("location", "lon", -180.01),
        ("schedule", "open_after_sunrise_min", -121),
        ("schedule", "open_after_sunrise_min", 181),
        ("schedule", "close_after_dusk_min", -121),
        ("schedule", "close_after_dusk_min", 181),
        ("safety", "move_timeout_s", 4),
        ("safety", "move_timeout_s", 121),
    ],
)
def test_validate_rejects_out_of_range(section, key, value):
    with pytest.raises(ConfigError):
        validate_config(_with(section, key, value))


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("location", "lat", 90),
        ("location", "lat", -90.0),
        ("location", "lon", -180),
        ("location", "lon", 180.0),
        ("schedule", "open_after_sunrise_min", -120),
        ("schedule", "close_after_dusk_min", 180),
        ("safety", "move_timeout_s", 5),
        ("safety", "move_timeout_s", 120),
    ],
)
def test_validate_accepts_boundaries(section, key, value):
    validate_config(_with(section, key, value))


# --- schedule ---


def _sun(date, alt):
    loc = DEFAULTS["location"]
    return sun_times_cet(*date, loc["lat"], loc["lon"], alt_deg=alt)


def test_schedule_summer_includes_dst():
    rise, _ = _sun(SUMMER, -0.833)
    _, dusk = _sun(SUMMER, -6)
    open_min, close_min = schedule(default_config(), *SUMMER)
    assert open_min == rise + 60 + 30
    assert close_min == dusk + 60 + 0


def test_schedule_winter_no_dst():
    rise, _ = _sun(WINTER, -0.833)
    _, dusk = _sun(WINTER, -6)
    open_min, close_min = schedule(default_config(), *WINTER)
    assert open_min == rise + 30
    assert close_min == dusk


def test_schedule_close_is_civil_dusk_plus_offset():
    cfg = _with("schedule", "close_after_dusk_min", -45)
    _, dusk = _sun(SUMMER, -6)
    _, close_min = schedule(cfg, *SUMMER)
    assert close_min == dusk + 60 - 45


@pytest.mark.parametrize("date", [(2026, 3, 29), (2026, 6, 15), (2026, 10, 25), (2026, 12, 21)])
def test_schedule_open_before_close(date):
    open_min, close_min = schedule(default_config(), *date)
    assert open_min < close_min


# --- schedule clamps: open_not_before_min / close_not_after_min ---


def test_default_clamps_are_none():
    assert DEFAULTS["schedule"]["open_not_before_min"] is None
    assert DEFAULTS["schedule"]["close_not_after_min"] is None


def test_clamp_none_keeps_offset_schedule():
    rise, _ = _sun(SUMMER, -0.833)
    _, dusk = _sun(SUMMER, -6)
    open_min, close_min = schedule(default_config(), *SUMMER)
    assert open_min == rise + 60 + 30
    assert close_min == dusk + 60


def test_clamp_open_not_before_raises_early_open():
    unclamped, _ = schedule(default_config(), *SUMMER)
    assert unclamped < 360  # June Tarnow: open earlier than 06:00 without clamp
    open_min, _ = schedule(_with("schedule", "open_not_before_min", 360), *SUMMER)
    assert open_min == 360


def test_clamp_open_not_before_no_effect_when_already_later():
    unclamped, _ = schedule(default_config(), *SUMMER)
    open_min, _ = schedule(_with("schedule", "open_not_before_min", 100), *SUMMER)
    assert open_min == unclamped


def test_clamp_close_not_after_lowers_late_close():
    unclamped_close = schedule(default_config(), *SUMMER)[1]
    assert unclamped_close > 1000
    _, close_min = schedule(_with("schedule", "close_not_after_min", 1000), *SUMMER)
    assert close_min == 1000


def test_clamp_close_not_after_no_effect_when_already_earlier():
    unclamped_close = schedule(default_config(), *SUMMER)[1]
    _, close_min = schedule(_with("schedule", "close_not_after_min", 1439), *SUMMER)
    assert close_min == unclamped_close


@pytest.mark.parametrize("key", ["open_not_before_min", "close_not_after_min"])
@pytest.mark.parametrize("value", [True, False, 1440, -1, 360.0, "360"])
def test_validate_rejects_bad_clamp(key, value):
    with pytest.raises(ConfigError):
        validate_config(_with("schedule", key, value))


@pytest.mark.parametrize("key", ["open_not_before_min", "close_not_after_min"])
@pytest.mark.parametrize("value", [None, 0, 360, 1439])
def test_validate_accepts_clamp(key, value):
    validate_config(_with("schedule", key, value))


def test_load_old_config_without_clamp_keys(tmp_path):
    data = {"schedule": {"open_after_sunrise_min": 45, "close_after_dusk_min": 10}}
    cfg = load(_write(tmp_path, "old.json", data))
    assert cfg["schedule"] == {
        "open_after_sunrise_min": 45,
        "close_after_dusk_min": 10,
        "open_not_before_min": None,
        "close_not_after_min": None,
    }
