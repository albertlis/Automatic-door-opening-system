# Phase 2 — Config System

## Goal

Implement the configuration schema, load/save, and validation. No hardware. No asyncio. Foundation that Phase 3+ depends on.

## Prerequisites

- Phase 1 complete (`src/astro.py` green)

## Deliverables

| File | Status |
|------|--------|
| `src/config.py` | create |
| `config.default.json` | create |
| `tests/test_config.py` | create |

## TDD Protocol

**Write `tests/test_config.py` first. All tests must fail. Then implement `src/config.py` until all pass.**

---

## 1. Config Schema (§5.5)

Full JSON shape:

```json
{
  "window": {
    "mode": "sun_position",
    "legacy": { "hour_open": 6, "hour_close": 18 },
    "sun": { "sunrise_offset_min": -30, "sunset_offset_min": 30 }
  },
  "override_open": {
    "mode": "dynamic",
    "fixed_hour": 8,
    "after_sunrise_min": 120
  },
  "override_close": {
    "mode": "fixed",
    "fixed_hour": 22,
    "after_sunset_min": 120
  },
  "light": {
    "lux_open": 8.0,
    "lux_close": 3.0
  },
  "safety": {
    "move_timeout_s": 21
  }
}
```

Three independent sections: `window`, `override_open`, `override_close` — each has its own `mode`.
Unused fields (e.g., `fixed_hour` when `mode=dynamic`) are kept in JSON, just ignored at runtime.

---

## 2. `src/config.py`

### Dataclasses

Use `@dataclass(slots=True)` (Python 3.13). All fields typed.

```python
from dataclasses import dataclass, asdict
import json

@dataclass(slots=True)
class WindowLegacy:
    hour_open: int = 6
    hour_close: int = 18

@dataclass(slots=True)
class WindowSun:
    sunrise_offset_min: int = -30
    sunset_offset_min: int = 30

@dataclass(slots=True)
class WindowConfig:
    mode: str = "sun_position"   # "sun_position" | "legacy"
    legacy: WindowLegacy = None  # populated in __post_init__
    sun: WindowSun = None

    def __post_init__(self):
        if self.legacy is None:
            self.legacy = WindowLegacy()
        if self.sun is None:
            self.sun = WindowSun()

@dataclass(slots=True)
class OverrideOpenConfig:
    mode: str = "dynamic"        # "dynamic" | "fixed"
    fixed_hour: int = 8
    after_sunrise_min: int = 120

@dataclass(slots=True)
class OverrideCloseConfig:
    mode: str = "fixed"          # "fixed" | "dynamic"
    fixed_hour: int = 22
    after_sunset_min: int = 120

@dataclass(slots=True)
class LightConfig:
    lux_open: float = 8.0
    lux_close: float = 3.0

@dataclass(slots=True)
class SafetyConfig:
    move_timeout_s: int = 21

@dataclass(slots=True)
class Config:
    window: WindowConfig = None
    override_open: OverrideOpenConfig = None
    override_close: OverrideCloseConfig = None
    light: LightConfig = None
    safety: SafetyConfig = None

    def __post_init__(self):
        if self.window is None:
            self.window = WindowConfig()
        if self.override_open is None:
            self.override_open = OverrideOpenConfig()
        if self.override_close is None:
            self.override_close = OverrideCloseConfig()
        if self.light is None:
            self.light = LightConfig()
        if self.safety is None:
            self.safety = SafetyConfig()
```

### ConfigError

```python
class ConfigError(Exception):
    pass
```

### `default_config() → Config`

Returns `Config()` with all defaults. Same values as `config.default.json`.

### `load(path: str) → Config`

Reads JSON file, deserializes nested dicts into dataclasses. Must handle missing optional keys by falling back to defaults (forward-compatibility). Do NOT strip unknown keys from JSON — but do not store them in Config either.

```python
def load(path: str) -> Config:
    with open(path) as f:
        data = json.load(f)
    return _from_dict(data)
```

Implement `_from_dict(data: dict) -> Config` that recursively builds the dataclass tree. Handle missing keys gracefully.

### `save(cfg: Config, path: str) -> None`

Serialize Config to JSON. Use `asdict()` from dataclasses but it doesn't handle nested dataclasses auto — implement `_to_dict(cfg)` manually or use `dataclasses.asdict(cfg)` (works correctly for nested dataclasses).

```python
def save(cfg: Config, path: str) -> None:
    with open(path, "w") as f:
        json.dump(_to_dict(cfg), f, indent=2)
```

### `validate_config(cfg, now_local_min, rise_cet, set_cet, dst_offset) → None`

Called at `POST /api/config` and on `load()`. Raises `ConfigError` on invariant violation.

```python
def validate_config(
    cfg: Config,
    now_local_min: int,
    rise_cet: int,
    set_cet: int,
    dst_offset: int,
) -> None:
    wo = _window_open_local(cfg, rise_cet, dst_offset)
    wc = _window_close_local(cfg, set_cet, dst_offset)
    ao = _abs_open_local(cfg, rise_cet, dst_offset)
    ac = _abs_close_local(cfg, set_cet, dst_offset)

    if ao < wo:
        raise ConfigError(
            f"abs_open ({ao // 60:02d}:{ao % 60:02d}) < "
            f"window_open ({wo // 60:02d}:{wo % 60:02d})"
        )
    if ac < wc:
        raise ConfigError(
            f"abs_close ({ac // 60:02d}:{ac % 60:02d}) < "
            f"window_close ({wc // 60:02d}:{wc % 60:02d})"
        )
    if ao >= ac:
        raise ConfigError(
            f"abs_open ({ao // 60:02d}:{ao % 60:02d}) >= "
            f"abs_close ({ac // 60:02d}:{ac % 60:02d})"
        )
```

### Helper functions (also used by `state.py`)

Export these — `state.py` imports them:

```python
def _window_open_local(cfg: Config, rise_cet: int, dst: int) -> int:
    if cfg.window.mode == "sun_position":
        return rise_cet + dst + cfg.window.sun.sunrise_offset_min
    return cfg.window.legacy.hour_open * 60

def _window_close_local(cfg: Config, set_cet: int, dst: int) -> int:
    if cfg.window.mode == "sun_position":
        return set_cet + dst + cfg.window.sun.sunset_offset_min
    return cfg.window.legacy.hour_close * 60

def _abs_open_local(cfg: Config, rise_cet: int, dst: int) -> int:
    if cfg.override_open.mode == "dynamic":
        return rise_cet + dst + cfg.override_open.after_sunrise_min
    return cfg.override_open.fixed_hour * 60

def _abs_close_local(cfg: Config, set_cet: int, dst: int) -> int:
    if cfg.override_close.mode == "dynamic":
        return set_cet + dst + cfg.override_close.after_sunset_min
    return cfg.override_close.fixed_hour * 60
```

Make these public (remove leading `_`) since they are imported by `state.py`.

---

## 3. `config.default.json`

Exact content matching §5.5:

```json
{
  "window": {
    "mode": "sun_position",
    "legacy": { "hour_open": 6, "hour_close": 18 },
    "sun": { "sunrise_offset_min": -30, "sunset_offset_min": 30 }
  },
  "override_open": {
    "mode": "dynamic",
    "fixed_hour": 8,
    "after_sunrise_min": 120
  },
  "override_close": {
    "mode": "fixed",
    "fixed_hour": 22,
    "after_sunset_min": 120
  },
  "light": {
    "lux_open": 8.0,
    "lux_close": 3.0
  },
  "safety": {
    "move_timeout_s": 21
  }
}
```

---

## 4. `tests/test_config.py`

### Required test cases

```python
from src.config import (
    Config, ConfigError, default_config, load, save, validate_config,
    window_open_local, window_close_local, abs_open_local, abs_close_local,
)
from src.astro import sun_times_cet, is_dst
import json, os, tempfile

# --- Default config ---

def test_default_config_loads():
    cfg = default_config()
    assert cfg.window.mode == "sun_position"
    assert cfg.override_open.mode == "dynamic"
    assert cfg.override_close.mode == "fixed"
    assert cfg.light.lux_open == 8.0
    assert cfg.light.lux_close == 3.0
    assert cfg.safety.move_timeout_s == 21

# --- Round-trip ---

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

def test_roundtrip_preserves_unused_fields():
    """Unused fields (e.g., fixed_hour when mode=dynamic) survive save/load."""
    cfg = default_config()
    cfg.override_open.mode = "dynamic"
    cfg.override_open.fixed_hour = 7   # unused but must persist
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        path = f.name
    try:
        save(cfg, path)
        loaded = load(path)
        assert loaded.override_open.fixed_hour == 7
    finally:
        os.unlink(path)

# --- load config.default.json ---

def test_load_default_json():
    cfg = load("config.default.json")
    assert cfg.window.mode == "sun_position"

# --- validate_config: valid cases ---

def test_validate_default_config_sun_position():
    cfg = default_config()
    rise, sset = sun_times_cet(2024, 6, 1)
    dst = 60  # summer
    validate_config(cfg, now_local_min=600, rise_cet=rise, set_cet=sset, dst_offset=dst)
    # Should not raise

def test_validate_legacy_mode():
    cfg = default_config()
    cfg.window.mode = "legacy"
    cfg.override_open.mode = "fixed"
    cfg.override_open.fixed_hour = 8   # abs_open = 480
    cfg.override_close.mode = "fixed"
    cfg.override_close.fixed_hour = 22  # abs_close = 1320
    # window_open = 6*60=360, window_close = 18*60=1080
    # 480>=360, 1320>=1080, 480<1320 → valid
    validate_config(cfg, now_local_min=0, rise_cet=300, set_cet=1100, dst_offset=0)

# --- validate_config: error cases ---

def test_validate_abs_open_before_window_open():
    cfg = default_config()
    cfg.window.mode = "legacy"
    cfg.window.legacy.hour_open = 8    # wo = 480
    cfg.override_open.mode = "fixed"
    cfg.override_open.fixed_hour = 6   # ao = 360 < 480 → error
    cfg.override_close.mode = "fixed"
    cfg.override_close.fixed_hour = 22
    import pytest
    with pytest.raises(ConfigError, match="abs_open"):
        validate_config(cfg, now_local_min=0, rise_cet=300, set_cet=1100, dst_offset=0)

def test_validate_abs_close_before_window_close():
    cfg = default_config()
    cfg.window.mode = "legacy"
    cfg.window.legacy.hour_close = 20  # wc = 1200
    cfg.override_close.mode = "fixed"
    cfg.override_close.fixed_hour = 18  # ac = 1080 < 1200 → error
    cfg.override_open.mode = "fixed"
    cfg.override_open.fixed_hour = 8
    import pytest
    with pytest.raises(ConfigError, match="abs_close"):
        validate_config(cfg, now_local_min=0, rise_cet=300, set_cet=1100, dst_offset=0)

def test_validate_abs_open_equals_abs_close():
    cfg = default_config()
    cfg.window.mode = "legacy"
    cfg.override_open.mode = "fixed"
    cfg.override_open.fixed_hour = 10   # ao = 600
    cfg.override_close.mode = "fixed"
    cfg.override_close.fixed_hour = 10  # ac = 600 → ao >= ac → error
    import pytest
    with pytest.raises(ConfigError, match=">="):
        validate_config(cfg, now_local_min=0, rise_cet=300, set_cet=1100, dst_offset=0)

# --- helper functions ---

def test_window_open_legacy():
    cfg = default_config()
    cfg.window.mode = "legacy"
    cfg.window.legacy.hour_open = 6
    assert window_open_local(cfg, rise_cet=200, dst=60) == 360  # 6*60, ignores rise+dst

def test_window_open_sun_position():
    cfg = default_config()
    cfg.window.mode = "sun_position"
    cfg.window.sun.sunrise_offset_min = -30
    # rise=200, dst=60 → 200+60-30 = 230
    assert window_open_local(cfg, rise_cet=200, dst=60) == 230

def test_abs_close_dynamic():
    cfg = default_config()
    cfg.override_close.mode = "dynamic"
    cfg.override_close.after_sunset_min = 120
    # set=1000, dst=60 → 1000+60+120 = 1180
    assert abs_close_local(cfg, set_cet=1000, dst=60) == 1180
```

---

## Acceptance Criteria

```bash
uv run pytest tests/test_config.py -v   # all green
uv run ruff check src/config.py tests/test_config.py
```

No `state.py`, no hardware, no asyncio.

## Notes

- `validate_config` is called both at `load()` time AND at `POST /api/config` (Phase 5). Do NOT call it inside `load()` automatically — let callers decide. `load()` should be permissive; explicit call to `validate_config` is separate.
- Helper functions (`window_open_local`, etc.) must be importable by `state.py` in Phase 3.
- `dataclasses.asdict()` works on CPython. On MicroPython, implement `_to_dict()` manually for `save()` if `asdict` is unavailable. The CPython implementation can use `asdict`; add a try/except fallback if needed.
