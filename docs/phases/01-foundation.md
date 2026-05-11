# Phase 1 — Project Scaffold + Astronomical Foundation

## Goal

Establish project tooling and implement pure astronomical/time functions that every later phase depends on. Zero hardware. Zero asyncio. Just math + shims.

## Prerequisites

None. This is the starting point.

## Deliverables

| File | Status |
|------|--------|
| `pyproject.toml` | create |
| `src/compat.py` | create |
| `src/astro.py` | create |
| `tests/test_astro.py` | create |

## TDD Protocol

**Write `tests/test_astro.py` first. All tests must fail. Then implement `src/astro.py` until all pass.**

---

## 1. `pyproject.toml`

Use `uv` as package manager. Python ≥ 3.13.

```toml
[project]
name = "smart-coop-v2"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "microdot[asyncio]>=2.0.6",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.4",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
target-version = "py313"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "SIM"]
```

Install: `uv sync --extra dev`

---

## 2. `src/compat.py`

Bridges MicroPython ↔ CPython. Imported by `state.py`, `hardware.py`, `main.py`.

```python
import sys

if sys.implementation.name == "micropython":
    from utime import ticks_ms, ticks_diff
    from uasyncio import sleep_ms
else:
    import time as _time

    def ticks_ms() -> int:
        return int(_time.time() * 1000)

    def ticks_diff(a: int, b: int) -> int:
        return a - b

    async def sleep_ms(ms: int) -> None:
        import asyncio
        await asyncio.sleep(ms / 1000)
```

**Rules:**
- No imports from `machine` or `uasyncio` at module level
- All callers import from `compat`, never directly from `utime`

---

## 3. `src/astro.py`

Pure functions, no state, no side effects. Usable on both CPython and MicroPython.

### Constants (Tarnów, ~50°N, 21°E — CET = UTC+1, no DST)

```
Summer solstice (DOY 172, 21 June):  rise=206 min, set=1193 min
Winter solstice (DOY 355, 21 Dec):   rise=453 min, set=943 min
```

Derived:
```python
_RISE_MEAN  = (453 + 206) / 2   # 329.5
_RISE_AMP   = (453 - 206) / 2   # 123.5
_SET_MEAN   = (943 + 1193) / 2  # 1068.0
_SET_AMP    = (1193 - 943) / 2  # 125.0
_SUMMER_DOY = 172
```

### Functions to implement

```python
def day_of_year(y: int, m: int, d: int) -> int:
    """Calendar day of year (1-based). Leap-year aware."""
    ...

def sun_times_cet(y: int, m: int, d: int) -> tuple[int, int]:
    """Return (sunrise_min, sunset_min) in CET (UTC+1), no DST.
    Uses cosine approximation. Accuracy ~±15min.
    """
    ...

def _day_of_week(y: int, m: int, d: int) -> int:
    """0=Sunday, 1=Monday, ..., 6=Saturday. Tomohiko Sakamoto algorithm."""
    ...

def _last_sunday(y: int, m: int) -> int:
    """Day-of-month of the last Sunday in month m of year y."""
    ...

def is_dst(y: int, m: int, d: int, h: int) -> bool:
    """True when CEST (summer time) is active.
    DS3231 stores CET always; DST offset applied in logic layer only.
    Start: last Sunday March at 02:00 CET.
    End:   last Sunday October at 02:00 CET.
    """
    ...

def local_minutes(y: int, m: int, d: int, h: int, minute: int) -> int:
    """Minutes since midnight in local time (CET or CEST).
    May exceed 1440 during CEST at CET 23:xx — callers must handle.
    """
    ...
```

### Implementation details

`day_of_year`:
```python
DAYS_BEFORE = [0, 0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
doy = DAYS_BEFORE[m] + d
if m > 2 and (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)):
    doy += 1
return doy
```

`sun_times_cet`:
```python
import math
angle = 2 * math.pi * (doy - _SUMMER_DOY) / 365
rise = int(_RISE_MEAN - _RISE_AMP * math.cos(angle))
sset = int(_SET_MEAN  + _SET_AMP  * math.cos(angle))
return rise, sset
```

`_day_of_week` (Tomohiko Sakamoto):
```python
t = [0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4]
if m < 3:
    y -= 1
return (y + y // 4 - y // 100 + y // 400 + t[m - 1] + d) % 7
```

`_last_sunday`:
```python
days_in_month = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
dim = days_in_month[m]
if m == 2 and (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)):
    dim = 29
dow = _day_of_week(y, m, dim)  # 0=Sunday
return dim - dow                # 0 if last day IS Sunday
```

`is_dst`:
```python
if m < 3 or m > 10:
    return False
if 3 < m < 10:
    return True
ls = _last_sunday(y, m)
if m == 3:
    if d != ls:
        return d > ls
    return h >= 2   # DST starts at 02:00 CET
else:  # m == 10
    if d != ls:
        return d < ls
    return h < 2    # DST ends at 02:00 CET
```

---

## 4. `tests/test_astro.py`

### Required test cases (write these FIRST)

```python
# Solstice verification (§5.3)
def test_summer_solstice():
    rise, sset = sun_times_cet(2024, 6, 21)
    assert rise == 206   # 03:26 CET
    assert sset == 1193  # 19:53 CET

def test_winter_solstice():
    rise, sset = sun_times_cet(2024, 12, 21)
    assert rise == 453   # 07:33 CET
    assert sset == 943   # 15:43 CET

# DST boundaries (§5.4) — 2024 dates
# 2024: DST starts last Sunday March = 31 March; ends last Sunday October = 27 October

def test_dst_before_start():
    assert is_dst(2024, 3, 31, 1) is False   # 01:59 CET still winter

def test_dst_at_start():
    assert is_dst(2024, 3, 31, 2) is True    # 02:00 CET → DST active

def test_dst_summer():
    assert is_dst(2024, 7, 1, 12) is True

def test_dst_before_end():
    assert is_dst(2024, 10, 27, 1) is True   # 01:59 CET still CEST

def test_dst_at_end():
    assert is_dst(2024, 10, 27, 2) is False  # 02:00 CET → winter time

def test_dst_winter():
    assert is_dst(2024, 1, 15, 12) is False

# local_minutes
def test_local_minutes_winter():
    # 2024-01-15 08:30 CET → 8*60+30 = 510, DST=False → +0
    assert local_minutes(2024, 1, 15, 8, 30) == 510

def test_local_minutes_summer():
    # 2024-07-01 08:30 CET → 510 + 60 (DST) = 570
    assert local_minutes(2024, 7, 1, 8, 30) == 570

# day_of_year
def test_doy_jan1():
    assert day_of_year(2024, 1, 1) == 1

def test_doy_dec31_leap():
    assert day_of_year(2024, 12, 31) == 366

def test_doy_dec31_nonleap():
    assert day_of_year(2023, 12, 31) == 365

def test_doy_mar1_leap():
    assert day_of_year(2024, 3, 1) == 61   # leap: Feb has 29 days

def test_doy_mar1_nonleap():
    assert day_of_year(2023, 3, 1) == 60

# last_sunday
def test_last_sunday_march_2024():
    # March 2024: last Sunday = 31
    from src.astro import _last_sunday
    assert _last_sunday(2024, 3) == 31

def test_last_sunday_october_2024():
    # October 2024: last Sunday = 27
    from src.astro import _last_sunday
    assert _last_sunday(2024, 10) == 27
```

Additional edge cases to add:
- `sun_times_cet` mid-year (e.g., March equinox) — verify within ±30 min of real value
- `is_dst` for months 1, 2, 4–9, 11, 12 (fast paths)
- `local_minutes` at midnight (h=0, minute=0)

---

## Acceptance Criteria

```bash
uv run pytest tests/test_astro.py -v   # all green
uv run ruff check src/ tests/          # no errors
uv run ruff format --check src/ tests/ # no changes needed
```

No `hardware.py`, no `state.py`, no asyncio — this phase is purely math.

## Notes

- `src/astro.py` is imported by `src/state.py` (Phase 3) and `src/config.py` (Phase 2). Keep it self-contained.
- `math` module is available in MicroPython — no workarounds needed.
- Do NOT add typing imports from `from __future__ import annotations` — not available in MicroPython. Use standard Python 3.13 type hints directly in function signatures (they work on CPython; on MicroPython they're stripped at compile time by `mpy-cross`).
