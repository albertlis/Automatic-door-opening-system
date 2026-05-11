# Phase 3 — State Machine Core

## Goal

Implement the synchronous heart of the system: `GateState` enum, `MovementTrigger` enum, and `CoopController.tick()` with all decision logic. No asyncio. No real hardware. Fully testable on CPython with mock objects.

## Prerequisites

- Phase 1 complete (`src/astro.py`)
- Phase 2 complete (`src/config.py`)

## Deliverables

| File | Status |
|------|--------|
| `src/state.py` | create (sync parts only) |
| `tests/mock_hardware.py` | create |
| `tests/conftest.py` | create |
| `tests/test_state.py` | create |

Phase 4 will add async coroutines (`_run_move`, task loops) to `src/state.py`.

## TDD Protocol

**Write `tests/test_state.py` first. All tests must fail. Then implement `src/state.py` until all pass.**

---

## 1. `tests/mock_hardware.py`

No imports from `machine`, `uasyncio`, or `utime`. Fully programmable from tests.

```python
class MockMotor:
    def __init__(self):
        self.commands: list[str] = []

    def forward(self) -> None:
        self.commands.append("forward")

    def backward(self) -> None:
        self.commands.append("backward")

    def stop(self) -> None:
        self.commands.append("stop")


class MockRTC:
    def __init__(self):
        # Default: synced, daytime, summer (CEST active)
        self._dt = (2024, 6, 1, 8, 0, 0, 0, 0)  # y,mo,d,h,min,sec,weekday,yearday

    def datetime(self) -> tuple:
        return self._dt

    def set_datetime(self, dt: tuple) -> None:
        self._dt = dt


class MockLightSensor:
    def __init__(self):
        self._lux: float = 10.0

    def read_lux(self) -> float:
        return self._lux

    def set_lux(self, v: float) -> None:
        self._lux = v


class MockButton:
    """Used for both limit switches and push buttons. value() mimics GPIO INPUT_PULLUP."""

    def __init__(self, active: bool = False):
        self._active = active  # True = pressed/triggered

    def value(self) -> int:
        return 0 if self._active else 1  # pull-up: 0=active, 1=inactive

    def activate(self) -> None:
        self._active = True

    def deactivate(self) -> None:
        self._active = False


class MockLED:
    def __init__(self):
        self.state: str = "off"
        self.blinking: bool = False

    def on(self) -> None:
        self.state = "on"
        self.blinking = False

    def off(self) -> None:
        self.state = "off"
        self.blinking = False

    def blink(self) -> None:
        self.blinking = True


class MockPCF:
    """Mock PCF8574 GPIO expander."""

    def __init__(self):
        self._pins: dict[int, int] = {}

    def set_pin(self, pin: int, val: int) -> None:
        self._pins[pin] = val

    def get_pin(self, pin: int) -> bool:
        return bool(self._pins.get(pin, 1))

    def read_all(self) -> int:
        result = 0xFF
        for pin, val in self._pins.items():
            if not val:
                result &= ~(1 << pin)
        return result


class MockNSleep:
    """Mock DRV8833 nSLEEP pin (GPIO21)."""

    def __init__(self):
        self._val: int = 0  # start in sleep

    def value(self, v: int | None = None) -> int:
        if v is not None:
            self._val = v
        return self._val


class MockNFault:
    """Mock DRV8833 nFAULT pin (GPIO20). active-low."""

    def __init__(self):
        self._val: int = 1  # HIGH = no fault

    def value(self) -> int:
        return self._val

    def trigger_fault(self) -> None:
        self._val = 0  # LOW = fault active
```

---

## 2. `tests/conftest.py`

```python
import pytest
from tests.mock_hardware import (
    MockMotor, MockRTC, MockLightSensor, MockButton,
    MockLED, MockPCF, MockNSleep, MockNFault,
)
from src.state import CoopController, GateState
from src.config import default_config


@pytest.fixture
def ctrl():
    config = default_config()
    # Use legacy mode + fixed overrides for deterministic test times
    config.window.mode = "legacy"
    config.window.legacy.hour_open = 6    # wo = 360 min
    config.window.legacy.hour_close = 18  # wc = 1080 min
    config.override_open.mode = "fixed"
    config.override_open.fixed_hour = 8   # ao = 480 min
    config.override_close.mode = "fixed"
    config.override_close.fixed_hour = 22  # ac = 1320 min

    c = CoopController(
        motor=MockMotor(),
        rtc=MockRTC(),
        light_sensor=MockLightSensor(),
        pcf=MockPCF(),
        limit_top=MockButton(active=False),
        limit_bottom=MockButton(active=False),
        btn_open=MockButton(),
        btn_close=MockButton(),
        leds=(MockLED(), MockLED(), MockLED()),  # red, yellow, green
        nsleep=MockNSleep(),
        nfault=MockNFault(),
        config=config,
    )
    # Standard start state for most tests
    c.state = GateState.IDLE_CLOSED
    c.lux_buffer = [10.0] * 5
    c.lux_ready = True
    return c
```

---

## 3. `src/state.py` — Sync Parts

### Enums

```python
from enum import Enum

class GateState(Enum):
    INIT = "INIT"
    IDLE_OPEN = "IDLE_OPEN"
    IDLE_CLOSED = "IDLE_CLOSED"
    MOVING_OPEN = "MOVING_OPEN"
    MOVING_CLOSE = "MOVING_CLOSE"
    MANUAL_HOLD_OPEN = "MANUAL_HOLD_OPEN"
    MANUAL_HOLD_CLOSED = "MANUAL_HOLD_CLOSED"
    SAFETY_STOP = "SAFETY_STOP"
    ERROR = "ERROR"


class MovementTrigger(Enum):
    AUTO = 0
    MANUAL = 1
    RECOVERY = 2
```

### `CoopController.__init__`

```python
class CoopController:
    def __init__(
        self,
        motor,
        rtc,
        light_sensor,
        pcf,
        limit_top,
        limit_bottom,
        btn_open,
        btn_close,
        leds,       # tuple: (led_red, led_yellow, led_green)
        nsleep,     # DRV8833 nSLEEP pin
        nfault,     # DRV8833 nFAULT pin
        config,
    ):
        self.motor = motor
        self.rtc = rtc
        self.light = light_sensor
        self.pcf = pcf
        self.limit_top = limit_top
        self.limit_bottom = limit_bottom
        self.btn_open = btn_open
        self.btn_close = btn_close
        self.leds = leds
        self.nsleep = nsleep
        self.nfault = nfault
        self.config = config

        self.state: GateState = GateState.INIT
        self._trigger: MovementTrigger = MovementTrigger.AUTO
        self._move_start_ms: int = 0
        self._abort_move: bool = False
        self._i2c_fail_count: int = 0
        self._vbat_low: bool = False
        self._config_warning: str = ""
        self._today_times: tuple = (0, 0, 0, 0)  # (wo, wc, ao, ac) min from midnight

        # Daily record for binary logs (Phase 6)
        self._today_rec: list = [0, 0xFFFF, 0xFFFF, 0, 0]  # [days, open_min, close_min, manual_count, error_count]
        self._today_rec_error_count: int = 0

        # Shared lux buffer (populated by light_sensor_loop)
        self.lux_buffer: list[float] = [0.0] * 5
        self.lux_ready: bool = False

        # Resolve times if RTC is synced
        y, mo, d, h, minute, *_ = self.rtc.datetime()
        if y >= 2020:
            self._resolve_times(y, mo, d, h, minute)
```

### `_resolve_times`

```python
def _resolve_times(self, y: int, m: int, d: int, h: int, minute: int) -> None:
    """Compute trigger times for today. Clamp if invariant violated. Called at init + midnight."""
    from src.astro import sun_times_cet, is_dst
    from src.config import window_open_local, window_close_local, abs_open_local, abs_close_local

    rise, sset = sun_times_cet(y, m, d)
    dst = 60 if is_dst(y, m, d, h) else 0

    wo = window_open_local(self.config, rise, dst)
    wc = window_close_local(self.config, sset, dst)
    ao = abs_open_local(self.config, rise, dst)
    ac = abs_close_local(self.config, sset, dst)

    warnings: list[str] = []
    if ao < wo:
        warnings.append(
            f"abs_open {ao // 60:02d}:{ao % 60:02d} < "
            f"window_open {wo // 60:02d}:{wo % 60:02d} — clamped"
        )
        ao = wo
    if ac < wc:
        warnings.append(
            f"abs_close {ac // 60:02d}:{ac % 60:02d} < "
            f"window_close {wc // 60:02d}:{wc % 60:02d} — clamped"
        )
        ac = wc
    if ao >= ac:
        warnings.append("abs_open >= abs_close — backstop conflict, verify config")

    self._config_warning = "; ".join(warnings)
    if self._config_warning:
        self._log_warning(self._config_warning)
    self._today_times = (wo, wc, ao, ac)
```

### `tick()`

**Synchronous. Called every 2s by `control_loop`. No asyncio inside.**

```python
def tick(self) -> None:
    from src.astro import local_minutes
    from compat import ticks_ms, ticks_diff

    # --- MOVING: check timeout ---
    if self.state in (GateState.MOVING_OPEN, GateState.MOVING_CLOSE):
        elapsed = ticks_diff(ticks_ms(), self._move_start_ms)
        if elapsed > self.config.safety.move_timeout_s * 1000:
            self._safety_stop()
        return  # while moving, only check timeout

    y, mo, d, h, minute, *_ = self._i2c_call(self.rtc.datetime)
    if y is None:
        return  # I2C failed, already transitioned to ERROR

    wo, wc, ao, ac = self._today_times
    now = local_minutes(y, mo, d, h, minute)

    lux_ok = self.lux_ready
    all_lux_high = lux_ok and all(v > self.config.light.lux_open for v in self.lux_buffer)
    all_lux_low  = lux_ok and all(v < self.config.light.lux_close for v in self.lux_buffer)

    sensor_open  = (wo <= now < wc) and all_lux_high
    sensor_close = (now >= wc or now < wo) and all_lux_low
    abs_open     = wo <= now < wc and ao <= now   # backstop inside window
    abs_close    = (now >= ac or now < wo)         # backstop in night zone

    if self.state == GateState.IDLE_CLOSED:
        if sensor_open or abs_open:
            self._trigger = MovementTrigger.AUTO
            self.state = GateState.MOVING_OPEN

    elif self.state == GateState.IDLE_OPEN:
        if sensor_close or abs_close:
            self._trigger = MovementTrigger.AUTO
            self.state = GateState.MOVING_CLOSE

    elif self.state == GateState.MANUAL_HOLD_OPEN:
        if abs_close:
            self._trigger = MovementTrigger.AUTO
            self.state = GateState.MOVING_CLOSE

    elif self.state == GateState.MANUAL_HOLD_CLOSED:
        if abs_open:
            self._trigger = MovementTrigger.AUTO
            self.state = GateState.MOVING_OPEN

    elif self.state == GateState.INIT:
        self._handle_init()
```

### `_handle_init()`

```python
def _handle_init(self) -> None:
    top = self.limit_top.value() == 0      # 0=active (pull-up)
    bottom = self.limit_bottom.value() == 0

    if top and bottom:
        self._enter_error()
        return
    if top:
        self.state = GateState.IDLE_OPEN
        return
    if bottom:
        self.state = GateState.IDLE_CLOSED
        return
    # Both inactive: decide by time
    if self.is_daytime():
        self._trigger = MovementTrigger.AUTO
        self.state = GateState.MOVING_OPEN
    else:
        self._trigger = MovementTrigger.AUTO
        self.state = GateState.MOVING_CLOSE
```

### `is_daytime() → bool`

```python
def is_daytime(self) -> bool:
    from src.astro import local_minutes
    y, mo, d, h, minute, *_ = self.rtc.datetime()
    wo, wc, *_ = self._today_times
    now = local_minutes(y, mo, d, h, minute)
    return wo <= now < wc
```

### `manual_move(action: str)`

```python
def manual_move(self, action: str) -> None:
    """Highest priority. Works from any state. Interrupts opposite movement."""
    import asyncio

    if action == "open" and self.state in (
        GateState.IDLE_OPEN, GateState.MANUAL_HOLD_OPEN, GateState.MOVING_OPEN
    ):
        return
    if action == "close" and self.state in (
        GateState.IDLE_CLOSED, GateState.MANUAL_HOLD_CLOSED, GateState.MOVING_CLOSE
    ):
        return

    if self.state in (GateState.MOVING_OPEN, GateState.MOVING_CLOSE):
        self._abort_move = True
        self.motor.stop()
        self.nsleep.value(0)

    trigger = MovementTrigger.RECOVERY if self.state == GateState.SAFETY_STOP else MovementTrigger.MANUAL
    self._trigger = trigger
    self.state = GateState.MOVING_OPEN if action == "open" else GateState.MOVING_CLOSE
    self._today_rec[3] += 1  # manual_count

    asyncio.create_task(self._run_move(action))
```

### `_safety_stop()`

```python
def _safety_stop(self) -> None:
    self.nsleep.value(0)   # nSLEEP=LOW (~2µA) — always, regardless of I2C
    self.motor.stop()      # IN1=IN2=LOW (coast) — best-effort via PCF8574
    if self.state != GateState.SAFETY_STOP:
        self._today_rec_error_count += 1
        self._today_rec[4] = self._today_rec_error_count
    self.state = GateState.SAFETY_STOP
```

### `simulate_timeout()` (test hook)

```python
def simulate_timeout(self) -> None:
    from compat import ticks_ms
    self._move_start_ms = ticks_ms() - (self.config.safety.move_timeout_s * 1000 + 1)
```

### `_i2c_call(fn)`

```python
def _i2c_call(self, fn):
    try:
        result = fn()
        self._i2c_fail_count = 0
        return result
    except OSError:
        self._i2c_fail_count += 1
        if self._i2c_fail_count >= 3:
            self._enter_error()
        return None
```

### `_enter_error()`

```python
def _enter_error(self) -> None:
    self.motor.stop()
    self.nsleep.value(0)
    self.state = GateState.ERROR
```

### `_on_midnight(y, m, d, h, minute)`

```python
def _on_midnight(self, y: int, m: int, d: int, h: int, minute: int) -> None:
    # Phase 6 will add: _write_record(self._today_rec)
    self._today_rec = [0, 0xFFFF, 0xFFFF, 0, 0]
    self._today_rec_error_count = 0
    self._resolve_times(y, m, d, h, minute)
```

### `_log_warning(msg)` (stub for now)

```python
def _log_warning(self, msg: str) -> None:
    print(f"[WARNING] {msg}")  # Phase 6 will write to binary log
```

### `status_json() → str`

```python
def status_json(self) -> str:
    import json
    from src.astro import local_minutes
    y, mo, d, h, minute, *_ = self.rtc.datetime()
    return json.dumps({
        "state": self.state.value,
        "lux": self.lux_buffer,
        "time": f"{h:02d}:{minute:02d}",
        "limit_top": self.limit_top.value() == 0,
        "limit_bottom": self.limit_bottom.value() == 0,
        "vbat_v": None,   # Phase 8 wires real ADC
        "config_warning": self._config_warning,
    })
```

### `get_forecast(days: int = 30) → list[tuple]`

```python
def get_forecast(self, days: int = 30) -> list[tuple]:
    """Returns [(wo, wc, ao, ac), ...] for next N days. Used by SVG chart (Phase 5)."""
    from src.astro import sun_times_cet, is_dst
    from src.config import window_open_local, window_close_local, abs_open_local, abs_close_local
    import time

    result = []
    y, mo, d, *_ = self.rtc.datetime()
    # Simple date arithmetic: iterate by incrementing epoch days
    epoch = _ymd_to_epoch(y, mo, d)
    for i in range(days):
        yi, mi, di = _epoch_to_ymd(epoch + i * 86400)
        rise, sset = sun_times_cet(yi, mi, di)
        dst = 60 if is_dst(yi, mi, di, 12) else 0  # use noon for DST check
        wo = window_open_local(self.config, rise, dst)
        wc = window_close_local(self.config, sset, dst)
        ao = abs_open_local(self.config, rise, dst)
        ac = abs_close_local(self.config, sset, dst)
        # Clamp (same logic as _resolve_times)
        ao = max(ao, wo)
        ac = max(ac, wc)
        result.append((wo, wc, ao, ac))
    return result
```

Implement `_ymd_to_epoch(y, m, d)` and `_epoch_to_ymd(epoch)` as module-level helpers using Python's `time.mktime` on CPython (and MicroPython equivalent).

---

## 4. `tests/test_state.py`

### Required test cases

```python
from src.state import CoopController, GateState, MovementTrigger

# --- INIT transitions ---

def test_init_limit_top_active(ctrl):
    ctrl.state = GateState.INIT
    ctrl.limit_top.activate()
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_OPEN

def test_init_limit_bottom_active(ctrl):
    ctrl.state = GateState.INIT
    ctrl.limit_bottom.activate()
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_CLOSED

def test_init_both_limits_active_is_error(ctrl):
    ctrl.state = GateState.INIT
    ctrl.limit_top.activate()
    ctrl.limit_bottom.activate()
    ctrl.tick()
    assert ctrl.state == GateState.ERROR

def test_init_no_limits_daytime_opens(ctrl):
    ctrl.state = GateState.INIT
    ctrl.rtc.set_datetime((2024, 6, 1, 10, 0, 0, 0, 0))  # 10:00, within 06-18 window
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_OPEN

def test_init_no_limits_nighttime_closes(ctrl):
    ctrl.state = GateState.INIT
    ctrl.rtc.set_datetime((2024, 6, 1, 20, 0, 0, 0, 0))  # 20:00, outside window
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_CLOSE

# --- Sensor-based open/close (§8.5 exact) ---

def test_morning_open(ctrl):
    ctrl.rtc.set_datetime((2024, 6, 1, 8, 1, 0, 0, 0))  # 08:01 within window
    ctrl.lux_buffer = [10.0] * 5                         # bright
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_OPEN

def test_evening_close(ctrl):
    ctrl.state = GateState.IDLE_OPEN
    ctrl.rtc.set_datetime((2024, 6, 1, 18, 1, 0, 0, 0))  # 18:01 outside window
    ctrl.lux_buffer = [1.0] * 5                           # dark
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_CLOSE

def test_cloud_no_premature_close(ctrl):
    """3/5 samples below threshold — no close."""
    ctrl.state = GateState.IDLE_OPEN
    ctrl.rtc.set_datetime((2024, 6, 1, 14, 0, 0, 0, 0))  # 14:00 inside window
    ctrl.lux_buffer = [10.0, 10.0, 1.0, 1.0, 10.0]       # partial darkness
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_OPEN  # no change

def test_no_open_when_dark_inside_window(ctrl):
    """Inside time window but lux low — do not open."""
    ctrl.rtc.set_datetime((2024, 6, 1, 9, 0, 0, 0, 0))
    ctrl.lux_buffer = [1.0] * 5  # dark (sensor not ready)
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_CLOSED

def test_no_open_before_window(ctrl):
    """Bright but before window_open — do not open."""
    ctrl.rtc.set_datetime((2024, 6, 1, 5, 0, 0, 0, 0))  # 05:00 before 06:00
    ctrl.lux_buffer = [50.0] * 5
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_CLOSED

# --- Absolute overrides ---

def test_absolute_close_overrides_light(ctrl):
    """22:00 → force close regardless of lux (§8.5 exact)."""
    ctrl.state = GateState.IDLE_OPEN
    ctrl.rtc.set_datetime((2024, 6, 1, 22, 0, 0, 0, 0))
    ctrl.lux_buffer = [50.0] * 5  # very bright
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_CLOSE

def test_absolute_open_overrides_low_lux(ctrl):
    """abs_open (08:00) hits inside window with lux_ready=False → still opens."""
    ctrl.lux_ready = False
    ctrl.rtc.set_datetime((2024, 6, 1, 8, 0, 0, 0, 0))  # ao=08:00
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_OPEN

# --- Manual hold blocks auto ---

def test_manual_hold_open_blocks_auto_close(ctrl):
    """MANUAL_HOLD_OPEN: auto close logic does not trigger (§8.5 exact)."""
    ctrl.state = GateState.MANUAL_HOLD_OPEN
    ctrl.rtc.set_datetime((2024, 6, 1, 19, 0, 0, 0, 0))  # after window_close
    ctrl.lux_buffer = [1.0] * 5
    ctrl.tick()
    assert ctrl.state == GateState.MANUAL_HOLD_OPEN

def test_manual_hold_closed_blocks_auto_open(ctrl):
    """MANUAL_HOLD_CLOSED: auto open logic does not trigger (§8.5 exact)."""
    ctrl.state = GateState.MANUAL_HOLD_CLOSED
    ctrl.rtc.set_datetime((2024, 6, 1, 9, 0, 0, 0, 0))
    ctrl.lux_buffer = [50.0] * 5
    ctrl.tick()
    assert ctrl.state == GateState.MANUAL_HOLD_CLOSED

def test_manual_hold_open_releases_on_abs_close(ctrl):
    """MANUAL_HOLD_OPEN: absolute close (22:00) overrides hold."""
    ctrl.state = GateState.MANUAL_HOLD_OPEN
    ctrl.rtc.set_datetime((2024, 6, 1, 22, 0, 0, 0, 0))  # ac=22:00
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_CLOSE
    assert ctrl._trigger == MovementTrigger.AUTO

def test_manual_hold_closed_releases_on_abs_open(ctrl):
    """MANUAL_HOLD_CLOSED: absolute open (08:00) overrides hold."""
    ctrl.state = GateState.MANUAL_HOLD_CLOSED
    ctrl.rtc.set_datetime((2024, 6, 1, 8, 0, 0, 0, 0))   # ao=08:00
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_OPEN
    assert ctrl._trigger == MovementTrigger.AUTO

# --- Safety stop ---

def test_safety_stop_on_timeout(ctrl):
    """Motor timeout → SAFETY_STOP + motor stopped (§8.5 exact)."""
    ctrl.state = GateState.MOVING_OPEN
    ctrl.simulate_timeout()
    ctrl.tick()
    assert ctrl.state == GateState.SAFETY_STOP
    assert "stop" in ctrl.motor.commands

def test_safety_stop_increments_error_count(ctrl):
    ctrl.state = GateState.MOVING_OPEN
    ctrl.simulate_timeout()
    ctrl.tick()
    assert ctrl._today_rec[4] == 1

def test_safety_stop_no_double_count(ctrl):
    """Second timeout while already in SAFETY_STOP must not increment again."""
    ctrl.state = GateState.SAFETY_STOP
    ctrl._safety_stop()
    assert ctrl._today_rec[4] == 0  # was 0, should stay 0

def test_nsleep_low_on_safety_stop(ctrl):
    ctrl.state = GateState.MOVING_OPEN
    ctrl.simulate_timeout()
    ctrl.tick()
    assert ctrl.nsleep.value() == 0

# --- I2C error handling ---

def test_i2c_single_failure_no_error(ctrl):
    call_count = 0
    def bad_rtc():
        nonlocal call_count
        call_count += 1
        raise OSError("I2C fail")

    ctrl._i2c_call(bad_rtc)
    assert ctrl.state != GateState.ERROR
    assert ctrl._i2c_fail_count == 1

def test_i2c_three_failures_enter_error(ctrl):
    def bad_fn():
        raise OSError("I2C fail")

    for _ in range(3):
        ctrl._i2c_call(bad_fn)

    assert ctrl.state == GateState.ERROR

def test_i2c_fail_resets_on_success(ctrl):
    def bad_fn():
        raise OSError()
    def good_fn():
        return (2024, 6, 1, 8, 0, 0, 0, 0)

    ctrl._i2c_call(bad_fn)
    ctrl._i2c_call(bad_fn)
    ctrl._i2c_call(good_fn)  # success resets counter
    assert ctrl._i2c_fail_count == 0

# --- resolve_times clamping ---

def test_resolve_times_clamps_abs_open_and_sets_warning(ctrl):
    """abs_open < window_open → clamp + warning message set."""
    ctrl.config.window.mode = "legacy"
    ctrl.config.window.legacy.hour_open = 10   # wo = 600
    ctrl.config.override_open.mode = "fixed"
    ctrl.config.override_open.fixed_hour = 8   # ao = 480 < 600 → must clamp to 600
    ctrl._resolve_times(2024, 6, 1, 8, 0)
    wo, wc, ao, ac = ctrl._today_times
    assert ao >= wo
    assert "clamped" in ctrl._config_warning

def test_resolve_times_no_warning_when_valid(ctrl):
    ctrl._resolve_times(2024, 6, 1, 8, 0)  # default config is valid
    assert ctrl._config_warning == ""

# --- lux_ready gate ---

def test_no_sensor_open_when_not_ready(ctrl):
    ctrl.lux_ready = False
    ctrl.rtc.set_datetime((2024, 6, 1, 9, 0, 0, 0, 0))
    ctrl.lux_buffer = [50.0] * 5
    ctrl.tick()
    # Should NOT open via sensor (abs_open at 08:00 might fire though)
    # Test at 09:00 which is inside window but before abs_open==08:00 already passed
    # Actually at 09:00 abs_open (08:00) fires because ao<=now, so this tests differently
    # Use a time that's inside window but before abs_open to isolate sensor logic
    ctrl.state = GateState.IDLE_CLOSED
    ctrl.lux_ready = False
    ctrl.rtc.set_datetime((2024, 6, 1, 7, 0, 0, 0, 0))  # 07:00, in window, before ao=08:00
    ctrl.lux_buffer = [50.0] * 5
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_CLOSED

# --- manual_move ---

def test_manual_move_open_from_idle_closed(ctrl):
    ctrl.manual_move("open")
    assert ctrl.state == GateState.MOVING_OPEN
    assert ctrl._trigger == MovementTrigger.MANUAL

def test_manual_move_ignored_when_already_open(ctrl):
    ctrl.state = GateState.IDLE_OPEN
    ctrl.manual_move("open")
    assert ctrl.state == GateState.IDLE_OPEN

def test_manual_move_from_safety_stop_uses_recovery_trigger(ctrl):
    ctrl.state = GateState.SAFETY_STOP
    ctrl.manual_move("open")
    assert ctrl._trigger == MovementTrigger.RECOVERY

def test_manual_move_increments_manual_count(ctrl):
    ctrl.manual_move("open")
    assert ctrl._today_rec[3] == 1
```

---

## Acceptance Criteria

```bash
uv run pytest tests/test_state.py -v   # all green
uv run ruff check src/state.py tests/mock_hardware.py tests/conftest.py tests/test_state.py
```

## Notes

- `tick()` is called by `control_loop` every 2s (Phase 4). It must be **purely synchronous** — no `await`, no `asyncio` imports at module level.
- `manual_move()` uses `asyncio.create_task()` — import asyncio locally inside the method, not at module level. On CPython test environment, this works because `asyncio` is available. In the test suite, `manual_move()` is called in sync context — the `asyncio.create_task()` line will run but the task won't execute (no event loop). Tests only check `ctrl.state` and `ctrl._trigger`, not actual motor movement.
- Phase 4 will add `_run_move`, `_on_limit_reached`, and the async task loops. Stub `_run_move` in this phase as a no-op coroutine so `asyncio.create_task` in `manual_move` doesn't crash:

```python
async def _run_move(self, direction: str) -> None:
    pass  # Phase 4 implements this
```
