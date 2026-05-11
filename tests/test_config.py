# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
import json

import pytest

from src.astro import sun_times_cet
from src.config import (
    ConfigError,
    abs_close_local,
    abs_open_local,
    default_config,
    load,
    save,
    validate_config,
    window_close_local,
    window_open_local,
)

# --- ConfigError ---


def test_config_error_is_exception():
    assert issubclass(ConfigError, Exception)
    assert str(ConfigError("boom")) == "boom"


# --- default_config ---


def test_default_config_loads():
    cfg = default_config()
    assert cfg.window.mode == "sun_position"
    assert cfg.override_open.mode == "dynamic"
    assert cfg.override_close.mode == "fixed"
    assert cfg.light.lux_open == 8.0
    assert cfg.light.lux_close == 3.0
    assert cfg.safety.move_timeout_s == 21


def test_default_config_nested_values():
    cfg = default_config()
    assert cfg.window.legacy.hour_open == 6
    assert cfg.window.legacy.hour_close == 18
    assert cfg.window.sun.sunrise_offset_min == -30
    assert cfg.window.sun.sunset_offset_min == 30
    assert cfg.override_open.fixed_hour == 8
    assert cfg.override_open.after_sunrise_min == 120
    assert cfg.override_close.fixed_hour == 22
    assert cfg.override_close.after_sunset_min == 120


def test_default_config_independence():
    """Two default_config() calls return independent objects — no shared mutable state."""
    c1 = default_config()
    c2 = default_config()
    c1.light.lux_open = 99.0
    assert c2.light.lux_open == 8.0


# --- round-trip ---


def test_roundtrip_save_load(tmp_path):
    cfg = default_config()
    path = str(tmp_path / "config.json")
    save(cfg, path)
    loaded = load(path)
    assert loaded.window.mode == cfg.window.mode
    assert loaded.window.sun.sunrise_offset_min == cfg.window.sun.sunrise_offset_min
    assert loaded.override_open.after_sunrise_min == cfg.override_open.after_sunrise_min
    assert loaded.override_close.fixed_hour == cfg.override_close.fixed_hour
    assert loaded.light.lux_open == cfg.light.lux_open
    assert loaded.safety.move_timeout_s == cfg.safety.move_timeout_s


def test_roundtrip_preserves_unused_fields(tmp_path):
    """Unused field (fixed_hour when mode=dynamic) survives save/load."""
    cfg = default_config()
    cfg.override_open.mode = "dynamic"
    cfg.override_open.fixed_hour = 7
    path = str(tmp_path / "config.json")
    save(cfg, path)
    loaded = load(path)
    assert loaded.override_open.fixed_hour == 7


def test_save_produces_valid_json(tmp_path):
    cfg = default_config()
    path = str(tmp_path / "config.json")
    save(cfg, path)
    with open(path) as f:
        data = json.loads(f.read())
    assert "window" in data
    assert "light" in data
    assert data["light"]["lux_open"] == 8.0
    assert data["window"]["mode"] == "sun_position"


# --- load config.default.json ---


def test_load_default_json():
    cfg = load("config.default.json")
    assert cfg.window.mode == "sun_position"


# --- load robustness ---


def test_load_missing_nested_key(tmp_path):
    """JSON without window.sun falls back to WindowSun defaults."""
    data = {"window": {"mode": "sun_position", "legacy": {"hour_open": 6, "hour_close": 18}}}
    path = str(tmp_path / "partial.json")
    with open(path, "w") as f:
        json.dump(data, f)
    cfg = load(path)
    assert cfg.window.sun.sunrise_offset_min == -30
    assert cfg.window.sun.sunset_offset_min == 30


def test_load_unknown_extra_keys(tmp_path):
    """Extra unknown keys in JSON ignored, no crash."""
    data = {
        "window": {
            "mode": "legacy",
            "legacy": {"hour_open": 6, "hour_close": 18},
            "sun": {"sunrise_offset_min": 0, "sunset_offset_min": 0},
            "unknown_field": "ignored",
        },
        "override_open": {"mode": "fixed", "fixed_hour": 8, "after_sunrise_min": 120},
        "override_close": {"mode": "fixed", "fixed_hour": 22, "after_sunset_min": 120},
        "light": {"lux_open": 8.0, "lux_close": 3.0},
        "safety": {"move_timeout_s": 21},
        "future_section": {"foo": "bar"},
    }
    path = str(tmp_path / "extra.json")
    with open(path, "w") as f:
        json.dump(data, f)
    cfg = load(path)
    assert cfg.window.mode == "legacy"


def test_load_empty_json(tmp_path):
    """Completely empty JSON dict → all defaults."""
    path = str(tmp_path / "empty.json")
    with open(path, "w") as f:
        json.dump({}, f)
    cfg = load(path)
    assert cfg.window.mode == "sun_position"
    assert cfg.light.lux_open == 8.0


# --- validate_config: valid ---


def test_validate_default_config_sun_position():
    cfg = default_config()
    rise, sset = sun_times_cet(2024, 6, 1)
    validate_config(cfg, now_local_min=600, rise_cet=rise, set_cet=sset, dst_offset=60)
    # must not raise


def test_validate_legacy_mode():
    cfg = default_config()
    cfg.window.mode = "legacy"
    cfg.override_open.mode = "fixed"
    cfg.override_open.fixed_hour = 8  # ao=480
    cfg.override_close.mode = "fixed"
    cfg.override_close.fixed_hour = 22  # ac=1320
    # wo=360, wc=1080; 480>=360, 1320>=1080, 480<1320 → valid
    validate_config(cfg, now_local_min=0, rise_cet=300, set_cet=1100, dst_offset=0)


def test_validate_ao_equals_wo_passes():
    """ao == wo is valid (ao < wo is the only failing condition)."""
    cfg = default_config()
    cfg.window.mode = "legacy"
    cfg.window.legacy.hour_open = 8  # wo=480
    cfg.override_open.mode = "fixed"
    cfg.override_open.fixed_hour = 8  # ao=480 == wo → still valid
    cfg.override_close.mode = "fixed"
    cfg.override_close.fixed_hour = 22  # ac=1320
    validate_config(cfg, now_local_min=0, rise_cet=300, set_cet=1100, dst_offset=0)


def test_validate_ac_equals_wc_passes():
    """ac == wc is valid (ac < wc is the only failing condition)."""
    cfg = default_config()
    cfg.window.mode = "legacy"
    cfg.window.legacy.hour_close = 18  # wc=1080
    cfg.override_open.mode = "fixed"
    cfg.override_open.fixed_hour = 8  # ao=480 >= wo=360 ✓
    cfg.override_close.mode = "fixed"
    cfg.override_close.fixed_hour = 18  # ac=1080 == wc=1080 → passes check 2
    validate_config(cfg, now_local_min=0, rise_cet=300, set_cet=1100, dst_offset=0)


# --- validate_config: errors ---


def test_validate_abs_open_before_window_open():
    cfg = default_config()
    cfg.window.mode = "legacy"
    cfg.window.legacy.hour_open = 8  # wo=480
    cfg.override_open.mode = "fixed"
    cfg.override_open.fixed_hour = 6  # ao=360 < wo=480 → error
    cfg.override_close.mode = "fixed"
    cfg.override_close.fixed_hour = 22
    with pytest.raises(ConfigError, match="abs_open"):
        validate_config(cfg, now_local_min=0, rise_cet=300, set_cet=1100, dst_offset=0)


def test_validate_abs_open_before_window_open_dynamic():
    """Dynamic modes: ao < wo when after_sunrise_min < sunrise_offset_min."""
    cfg = default_config()
    cfg.window.mode = "sun_position"
    cfg.window.sun.sunrise_offset_min = 60  # wo = 300+0+60 = 360
    cfg.override_open.mode = "dynamic"
    cfg.override_open.after_sunrise_min = 30  # ao = 300+0+30 = 330 < 360 → error
    cfg.override_close.mode = "fixed"
    cfg.override_close.fixed_hour = 22
    with pytest.raises(ConfigError, match="abs_open"):
        validate_config(cfg, now_local_min=0, rise_cet=300, set_cet=1100, dst_offset=0)


def test_validate_abs_close_before_window_close():
    cfg = default_config()
    cfg.window.mode = "legacy"
    cfg.window.legacy.hour_close = 20  # wc=1200
    cfg.override_open.mode = "fixed"
    cfg.override_open.fixed_hour = 8  # ao=480 >= wo=360 → passes check 1
    cfg.override_close.mode = "fixed"
    cfg.override_close.fixed_hour = 18  # ac=1080 < wc=1200 → error
    with pytest.raises(ConfigError, match="abs_close"):
        validate_config(cfg, now_local_min=0, rise_cet=300, set_cet=1100, dst_offset=0)


def test_validate_abs_open_equals_abs_close():
    """ao == ac raises (>= condition)."""
    cfg = default_config()
    cfg.window.mode = "legacy"
    cfg.window.legacy.hour_close = 10  # wc=600 so ac=600 passes check 2
    cfg.override_open.mode = "fixed"
    cfg.override_open.fixed_hour = 10  # ao=600
    cfg.override_close.mode = "fixed"
    cfg.override_close.fixed_hour = 10  # ac=600 == ao → ao >= ac → error
    with pytest.raises(ConfigError, match=">="):
        validate_config(cfg, now_local_min=0, rise_cet=300, set_cet=1100, dst_offset=0)


def test_validate_abs_open_greater_than_abs_close():
    """ao > ac raises (>= condition)."""
    cfg = default_config()
    cfg.window.mode = "legacy"
    cfg.window.legacy.hour_close = 10  # wc=600 so ac=600 passes check 2 (not strict)
    cfg.override_open.mode = "fixed"
    cfg.override_open.fixed_hour = 12  # ao=720
    cfg.override_close.mode = "fixed"
    cfg.override_close.fixed_hour = 10  # ac=600, ac >= wc=600 passes check 2
    # ao=720 >= ac=600 → error
    with pytest.raises(ConfigError, match=">="):
        validate_config(cfg, now_local_min=0, rise_cet=300, set_cet=1100, dst_offset=0)


# --- helper functions: 4 functions × 2 modes = 8 tests ---


def test_window_open_legacy():
    cfg = default_config()
    cfg.window.mode = "legacy"
    cfg.window.legacy.hour_open = 6
    assert window_open_local(cfg, rise_cet=200, dst=60) == 360  # 6*60, ignores rise+dst


def test_window_open_sun_position():
    cfg = default_config()
    cfg.window.mode = "sun_position"
    cfg.window.sun.sunrise_offset_min = -30
    # rise=200, dst=60 → 200+60-30=230
    assert window_open_local(cfg, rise_cet=200, dst=60) == 230


def test_window_close_legacy():
    cfg = default_config()
    cfg.window.mode = "legacy"
    cfg.window.legacy.hour_close = 18
    assert window_close_local(cfg, set_cet=1000, dst=60) == 1080  # 18*60, ignores set+dst


def test_window_close_sun_position():
    cfg = default_config()
    cfg.window.mode = "sun_position"
    cfg.window.sun.sunset_offset_min = 30
    # set=1000, dst=60 → 1000+60+30=1090
    assert window_close_local(cfg, set_cet=1000, dst=60) == 1090


def test_abs_open_fixed():
    cfg = default_config()
    cfg.override_open.mode = "fixed"
    cfg.override_open.fixed_hour = 8
    assert abs_open_local(cfg, rise_cet=200, dst=60) == 480  # 8*60, ignores rise+dst


def test_abs_open_dynamic():
    cfg = default_config()
    cfg.override_open.mode = "dynamic"
    cfg.override_open.after_sunrise_min = 120
    # rise=200, dst=60 → 200+60+120=380
    assert abs_open_local(cfg, rise_cet=200, dst=60) == 380


def test_abs_close_fixed():
    cfg = default_config()
    cfg.override_close.mode = "fixed"
    cfg.override_close.fixed_hour = 22
    assert abs_close_local(cfg, set_cet=1000, dst=60) == 1320  # 22*60


def test_abs_close_dynamic():
    cfg = default_config()
    cfg.override_close.mode = "dynamic"
    cfg.override_close.after_sunset_min = 120
    # set=1000, dst=60 → 1000+60+120=1180
    assert abs_close_local(cfg, set_cet=1000, dst=60) == 1180
