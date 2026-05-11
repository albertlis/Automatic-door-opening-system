# Phase 4 — Async Tasks

## Goal

Implement the async coroutines: `_run_move` (motor control), `light_sensor_loop`, `control_loop`, `led_loop`, `button_monitor`. Wire them into `main.py`. All still testable on CPython with mock hardware.

## Prerequisites

- Phase 3 complete (`src/state.py` sync parts, `tests/test_state.py` green)

## Deliverables

| File | Status |
|------|--------|
| `src/state.py` | extend (add `_run_move`, `_on_limit_reached`) |
| `src/main.py` | create (async tasks, `start_ap_session` stub) |
| `tests/test_async.py` | create |

## TDD Protocol

**Write `tests/test_async.py` first. All tests must fail. Then implement until all pass.**

---

## 1. Extend `src/state.py` — Async Methods

### `_run_move(direction: str)` coroutine (§4.6 exact)

```python
async def _run_move(self, direction: str) -> None:
    import asyncio
    from compat import ticks_ms, sleep_ms

    self._move_start_ms = ticks_ms()
    self.nsleep.value(1)          # wake DRV8833
    await sleep_ms(1)             # 1ms wake-up delay

    if direction == "open":
        self.motor.forward()
        limit_pin = self.limit_top
    else:
        self.motor.backward()
        limit_pin = self.limit_bottom

    while limit_pin.value() == 1:   # HIGH = not triggered (pull-up)
        if self._abort_move:
            self._abort_move = False
            self.motor.stop()
            self.nsleep.value(0)
            return
        if self.nfault.value() == 0:   # nFAULT LOW = fault (active-low)
            self._safety_stop()
            return
        if self.state == GateState.SAFETY_STOP:   # timeout detected by tick()
            return
        await sleep_ms(20)

    self.motor.stop()
    self.nsleep.value(0)
    self._on_limit_reached()
```

### `_on_limit_reached()`

```python
def _on_limit_reached(self) -> None:
    """Transition to IDLE or MANUAL_HOLD based on _trigger."""
    if self._trigger == MovementTrigger.MANUAL:
        if self.state == GateState.MOVING_OPEN:
            self.state = GateState.MANUAL_HOLD_OPEN
            # Record first open
            y, mo, d, h, minute, *_ = self.rtc.datetime()
            from src.astro import local_minutes
            now = local_minutes(y, mo, d, h, minute)
            if self._today_rec[1] == 0xFFFF:
                self._today_rec[1] = now
        else:
            self.state = GateState.MANUAL_HOLD_CLOSED
            # Record last close
            y, mo, d, h, minute, *_ = self.rtc.datetime()
            from src.astro import local_minutes
            now = local_minutes(y, mo, d, h, minute)
            self._today_rec[2] = now
    else:  # AUTO or RECOVERY
        if self.state == GateState.MOVING_OPEN:
            self.state = GateState.IDLE_OPEN
            y, mo, d, h, minute, *_ = self.rtc.datetime()
            from src.astro import local_minutes
            now = local_minutes(y, mo, d, h, minute)
            if self._today_rec[1] == 0xFFFF:
                self._today_rec[1] = now
        else:
            self.state = GateState.IDLE_CLOSED
            y, mo, d, h, minute, *_ = self.rtc.datetime()
            from src.astro import local_minutes
            now = local_minutes(y, mo, d, h, minute)
            self._today_rec[2] = now
```

---

## 2. `src/main.py`

Async entry point and all background tasks. Imports real hardware objects when running on ESP32 (Phase 8 wires this up). For now, `create_hardware()` returns mocks so `python run_local.py` works.

```python
import asyncio
from src.state import CoopController, GateState
from compat import ticks_ms, ticks_diff, sleep_ms

# Constants
CLICK_WINDOW_MS = 500
DEBOUNCE_MS = 50
AP_TIMEOUT_MS = 10 * 60 * 1000

_last_midnight_day = -1
_ap_active = False
_last_http_ms = 0

ctrl: CoopController = None  # set by main() or run_local.py


# --- Async tasks ---

async def light_sensor_loop() -> None:
    """Collect 5 lux samples, rotate buffer. (§4.5)"""
    i = 0
    while True:
        val = ctrl._i2c_call(lambda: ctrl.light.read_lux())
        if val is not None:
            ctrl.lux_buffer[i] = val
            i = (i + 1) % 5
            if i == 0:
                ctrl.lux_ready = True
        await sleep_ms(300)


async def control_loop() -> None:
    """Drive state machine every 2s. (§4.3)"""
    global _last_midnight_day
    while True:
        y, mo, d, h, minute, *_ = ctrl.rtc.datetime()

        if y < 2020:   # DS3231 not synced yet
            await asyncio.sleep(2)
            continue

        if h == 0 and minute == 0 and d != _last_midnight_day:
            ctrl._on_midnight(y, mo, d, h, minute)
            _last_midnight_day = d

        prev_state = ctrl.state
        ctrl.tick()
        new_state = ctrl.state

        if new_state == GateState.MOVING_OPEN and prev_state != GateState.MOVING_OPEN:
            asyncio.create_task(ctrl._run_move("open"))
        elif new_state == GateState.MOVING_CLOSE and prev_state != GateState.MOVING_CLOSE:
            asyncio.create_task(ctrl._run_move("close"))

        await asyncio.sleep(2)


async def led_loop() -> None:
    """Update LED signals based on current state. (§LED table)"""
    led_red, led_yellow, led_green = ctrl.leds
    tick = 0
    while True:
        state = ctrl.state
        on_half = (tick % 2) == 0   # 1Hz blink: on for 1 tick, off for 1 tick (500ms each)
        fast_on = (tick % 2) == 0   # 4Hz: use 125ms sleep for ERROR state

        # Green LED
        if state in (GateState.IDLE_OPEN, GateState.MANUAL_HOLD_OPEN):
            led_green.on()
        elif state in (GateState.MOVING_OPEN, GateState.MOVING_CLOSE):
            led_green.on() if on_half else led_green.off()
        else:
            led_green.off()

        # Red LED
        if state in (GateState.IDLE_CLOSED, GateState.MANUAL_HOLD_CLOSED):
            led_red.on()
        elif state == GateState.SAFETY_STOP:
            led_red.on() if on_half else led_red.off()  # 1Hz
        elif state == GateState.ERROR:
            led_red.on() if on_half else led_red.off()  # 4Hz (loop runs at 125ms for ERROR)
        else:
            led_red.off()

        # Yellow LED (battery low — overlays any state)
        if ctrl._vbat_low:
            led_yellow.on() if on_half else led_yellow.off()
        else:
            led_yellow.off()

        tick += 1
        # Sleep duration depends on state: ERROR=125ms (4Hz), others=500ms (1Hz)
        sleep_dur = 125 if state == GateState.ERROR else 500
        await sleep_ms(sleep_dur)


async def button_monitor(btn, action: str) -> None:
    """Detect single click (manual_move) vs double-click (AP session). (§9.1)"""
    click_count = 0
    deadline = 0

    while True:
        if btn.value() == 0:   # pressed (pull-up)
            await sleep_ms(DEBOUNCE_MS)
            if btn.value() != 0:   # spurious
                continue
            click_count += 1
            deadline = ticks_ms() + CLICK_WINDOW_MS
            while btn.value() == 0:   # wait for release
                await sleep_ms(10)

        if click_count > 0 and ticks_diff(deadline, ticks_ms()) <= 0:
            if click_count == 1:
                ctrl.manual_move(action)
            else:
                asyncio.create_task(start_ap_session())
            click_count = 0

        await sleep_ms(20)


async def start_ap_session() -> None:
    """Start WiFi AP + microdot web server. On CPython: no-op stub."""
    global _ap_active
    if _ap_active:
        return
    _ap_active = True
    try:
        import sys
        if sys.implementation.name == "micropython":
            import machine, network
            machine.freq(80_000_000)
            ap = network.WLAN(network.AP_IF)
            ap.active(True)
            ap.config(ssid="Coop_Control", password="coop123")
            from src.web import create_app
            app = create_app(ctrl)
            asyncio.create_task(_ap_watchdog(ap, app))
            await app.start_server(port=80)
            ap.active(False)
            machine.freq(40_000_000)
        else:
            # CPython: just start the server directly (used by run_local.py)
            from src.web import create_app
            app = create_app(ctrl)
            await app.start_server(host="localhost", port=5000)
    finally:
        _ap_active = False


async def _ap_watchdog(ap, server) -> None:
    global _last_http_ms
    _last_http_ms = ticks_ms()
    while True:
        await asyncio.sleep(60)
        idle = ticks_diff(ticks_ms(), _last_http_ms)
        if idle > AP_TIMEOUT_MS:
            server.shutdown()
            ap.active(False)
            return


async def main() -> None:
    global ctrl
    if ctrl is None:
        ctrl = _create_hardware_ctrl()

    asyncio.create_task(light_sensor_loop())
    asyncio.create_task(control_loop())
    asyncio.create_task(led_loop())
    asyncio.create_task(button_monitor(ctrl.btn_open, "open"))
    asyncio.create_task(button_monitor(ctrl.btn_close, "close"))

    # First-boot: DS3231 default date 2000-01-01
    y, mo, d, *_ = ctrl.rtc.datetime()
    if y == 2000 and mo == 1 and d == 1:
        await start_ap_session()

    while True:
        await asyncio.sleep(3600)


def _create_hardware_ctrl() -> CoopController:
    """Create CoopController with real hardware (ESP32) or mocks (CPython)."""
    import sys
    if sys.implementation.name == "micropython":
        from src.hardware import Motor, RTC, LightSensor, Button, LED, PCF8574, NSleepPin, NFaultPin
        from machine import I2C, Pin
        i2c = I2C(0, sda=Pin(4), scl=Pin(5), freq=400_000)
        pcf = PCF8574(i2c, 0x20)
        from src.config import load
        try:
            cfg = load("/config.json")
        except OSError:
            from src.config import default_config
            cfg = default_config()
        return CoopController(
            motor=Motor(pcf, in1=2, in2=3),
            rtc=RTC(i2c),
            light_sensor=LightSensor(i2c),
            pcf=pcf,
            limit_top=Button(Pin(0, Pin.IN, Pin.PULL_UP)),
            limit_bottom=Button(Pin(1, Pin.IN, Pin.PULL_UP)),
            btn_open=Button(Pin(7, Pin.IN, Pin.PULL_UP)),
            btn_close=Button(Pin(10, Pin.IN, Pin.PULL_UP)),
            leds=(LED(Pin(6), active_low=False), LED(pcf, pin=1, active_low=True), LED(pcf, pin=0, active_low=True)),
            nsleep=Pin(21, Pin.OUT),
            nfault=Pin(20, Pin.IN),
            config=cfg,
        )
    else:
        from tests.mock_hardware import MockMotor, MockRTC, MockLightSensor, MockButton, MockLED, MockPCF, MockNSleep, MockNFault
        from src.config import default_config
        return CoopController(
            motor=MockMotor(), rtc=MockRTC(), light_sensor=MockLightSensor(),
            pcf=MockPCF(), limit_top=MockButton(), limit_bottom=MockButton(),
            btn_open=MockButton(), btn_close=MockButton(),
            leds=(MockLED(), MockLED(), MockLED()),
            nsleep=MockNSleep(), nfault=MockNFault(),
            config=default_config(),
        )


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 3. `tests/test_async.py`

All tests use `pytest-asyncio`. Configure `asyncio_mode = "auto"` in `pyproject.toml` (done in Phase 1).

```python
import asyncio
import pytest
from src.state import CoopController, GateState, MovementTrigger
from tests.mock_hardware import MockButton

# Helper: activate a limit switch after a short delay (simulates motor reaching end-stop)
async def activate_after(btn: MockButton, delay_ms: int) -> None:
    await asyncio.sleep(delay_ms / 1000)
    btn.activate()


# --- _run_move: limit reached ---

@pytest.mark.asyncio
async def test_run_move_open_reaches_limit_idle(ctrl):
    ctrl.state = GateState.MOVING_OPEN
    ctrl._trigger = MovementTrigger.AUTO
    # Schedule limit activation after 50ms
    asyncio.create_task(activate_after(ctrl.limit_top, 50))
    await ctrl._run_move("open")
    assert ctrl.state == GateState.IDLE_OPEN
    assert "forward" in ctrl.motor.commands
    assert "stop" in ctrl.motor.commands
    assert ctrl.nsleep.value() == 0   # nSLEEP=LOW after stop

@pytest.mark.asyncio
async def test_run_move_open_manual_trigger_goes_to_manual_hold(ctrl):
    ctrl.state = GateState.MOVING_OPEN
    ctrl._trigger = MovementTrigger.MANUAL
    asyncio.create_task(activate_after(ctrl.limit_top, 50))
    await ctrl._run_move("open")
    assert ctrl.state == GateState.MANUAL_HOLD_OPEN

@pytest.mark.asyncio
async def test_run_move_close_reaches_limit_idle(ctrl):
    ctrl.state = GateState.MOVING_CLOSE
    ctrl._trigger = MovementTrigger.AUTO
    asyncio.create_task(activate_after(ctrl.limit_bottom, 50))
    await ctrl._run_move("close")
    assert ctrl.state == GateState.IDLE_CLOSED
    assert "backward" in ctrl.motor.commands

@pytest.mark.asyncio
async def test_run_move_recovery_trigger_goes_to_idle(ctrl):
    ctrl.state = GateState.MOVING_OPEN
    ctrl._trigger = MovementTrigger.RECOVERY
    asyncio.create_task(activate_after(ctrl.limit_top, 50))
    await ctrl._run_move("open")
    assert ctrl.state == GateState.IDLE_OPEN

# --- _run_move: abort ---

@pytest.mark.asyncio
async def test_run_move_aborted_by_abort_flag(ctrl):
    ctrl.state = GateState.MOVING_OPEN
    ctrl._trigger = MovementTrigger.AUTO

    async def abort_after_delay():
        await asyncio.sleep(0.05)
        ctrl._abort_move = True

    asyncio.create_task(abort_after_delay())
    await ctrl._run_move("open")
    assert ctrl._abort_move is False   # flag cleared
    assert "stop" in ctrl.motor.commands

# --- _run_move: nFAULT ---

@pytest.mark.asyncio
async def test_run_move_nfault_triggers_safety_stop(ctrl):
    ctrl.state = GateState.MOVING_OPEN
    ctrl._trigger = MovementTrigger.AUTO

    async def fault_after_delay():
        await asyncio.sleep(0.05)
        ctrl.nfault.trigger_fault()

    asyncio.create_task(fault_after_delay())
    await ctrl._run_move("open")
    assert ctrl.state == GateState.SAFETY_STOP

# --- _run_move: timeout via tick() ---

@pytest.mark.asyncio
async def test_run_move_exits_on_safety_stop_state(ctrl):
    """tick() sets state=SAFETY_STOP → _run_move exits without reaching limit."""
    ctrl.state = GateState.MOVING_OPEN
    ctrl._trigger = MovementTrigger.AUTO

    async def timeout_after_delay():
        await asyncio.sleep(0.05)
        ctrl.state = GateState.SAFETY_STOP  # simulate tick() timeout

    asyncio.create_task(timeout_after_delay())
    await ctrl._run_move("open")
    # Test passes if coroutine returns (doesn't loop forever)

# --- light_sensor_loop ---

@pytest.mark.asyncio
async def test_light_sensor_loop_fills_buffer(ctrl):
    from src.main import light_sensor_loop
    import src.main as main_module
    main_module.ctrl = ctrl
    ctrl.light.set_lux(42.0)
    ctrl.lux_ready = False

    task = asyncio.create_task(light_sensor_loop())
    await asyncio.sleep(0.4 * 5 + 0.1)   # 5 samples × 300ms + margin
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert ctrl.lux_ready is True
    assert all(v == pytest.approx(42.0) for v in ctrl.lux_buffer)

# --- control_loop ---

@pytest.mark.asyncio
async def test_control_loop_skips_when_rtc_unsynced(ctrl):
    from src.main import control_loop
    import src.main as main_module
    main_module.ctrl = ctrl
    ctrl.rtc.set_datetime((1999, 1, 1, 8, 0, 0, 0, 0))  # year < 2020
    ctrl.state = GateState.IDLE_CLOSED
    ctrl.lux_buffer = [50.0] * 5
    ctrl.lux_ready = True

    task = asyncio.create_task(control_loop())
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert ctrl.state == GateState.IDLE_CLOSED  # tick() never ran

@pytest.mark.asyncio
async def test_control_loop_spawns_run_move_on_state_change(ctrl):
    """control_loop detects MOVING_OPEN transition → creates _run_move task."""
    from src.main import control_loop
    import src.main as main_module
    main_module.ctrl = ctrl

    # Set up: bright, inside window → tick() will transition to MOVING_OPEN
    ctrl.state = GateState.IDLE_CLOSED
    ctrl.lux_buffer = [50.0] * 5
    ctrl.lux_ready = True
    ctrl.rtc.set_datetime((2024, 6, 1, 9, 0, 0, 0, 0))  # 09:00, inside window

    # Activate limit_top so _run_move exits immediately
    ctrl.limit_top.activate()

    task = asyncio.create_task(control_loop())
    await asyncio.sleep(2.5)  # wait for one control_loop iteration (2s sleep)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # State should have advanced (MOVING_OPEN → IDLE_OPEN via _run_move)
    assert ctrl.state in (GateState.MOVING_OPEN, GateState.IDLE_OPEN, GateState.MANUAL_HOLD_OPEN)

# --- button_monitor ---

@pytest.mark.asyncio
async def test_button_monitor_single_click_calls_manual_move(ctrl):
    from src.main import button_monitor
    import src.main as main_module
    main_module.ctrl = ctrl

    called_with = []
    original = ctrl.manual_move
    def mock_manual(action):
        called_with.append(action)
    ctrl.manual_move = mock_manual

    async def press_and_release(btn):
        await asyncio.sleep(0.01)
        btn._active = True
        await asyncio.sleep(0.1)
        btn._active = False

    asyncio.create_task(press_and_release(ctrl.btn_open))
    task = asyncio.create_task(button_monitor(ctrl.btn_open, "open"))
    await asyncio.sleep(0.8)   # > CLICK_WINDOW_MS (500ms) + margin
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert "open" in called_with

@pytest.mark.asyncio
async def test_button_monitor_double_click_schedules_ap(ctrl):
    from src.main import button_monitor
    import src.main as main_module
    main_module.ctrl = ctrl

    ap_started = []
    original_start = __import__("src.main", fromlist=["start_ap_session"]).start_ap_session

    async def mock_ap():
        ap_started.append(True)

    # Patch start_ap_session
    import src.main as m
    m.start_ap_session = mock_ap

    async def double_press(btn):
        for _ in range(2):
            await asyncio.sleep(0.01)
            btn._active = True
            await asyncio.sleep(0.05)
            btn._active = False
            await asyncio.sleep(0.05)

    asyncio.create_task(double_press(ctrl.btn_open))
    task = asyncio.create_task(button_monitor(ctrl.btn_open, "open"))
    await asyncio.sleep(0.9)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Restore
    m.start_ap_session = original_start
    assert len(ap_started) >= 1
```

---

## Acceptance Criteria

```bash
uv run pytest tests/test_async.py -v   # all green
uv run ruff check src/state.py src/main.py tests/test_async.py
```

## Notes

- `compat.sleep_ms` on CPython uses `asyncio.sleep(ms/1000)`. This means tests with `await sleep_ms(300)` in `light_sensor_loop` run in real time — tests use short sleep values by cancelling tasks early.
- `asyncio_mode = "auto"` in `pyproject.toml` means all async test functions are automatically treated as asyncio tests.
- `_run_move` starts `_move_start_ms` at entry. The `simulate_timeout()` test hook (Phase 3) only works for `tick()` — not `_run_move`. `_run_move`'s timeout check reads `ctrl.state == GateState.SAFETY_STOP` which `tick()` sets.
- The LED 4Hz blink for `ERROR` state: `led_loop` needs to sleep 125ms per tick. The pattern `(tick % 2)` gives 250ms on/off = 4Hz. Verify math before implementation.
- `manual_move` calls `asyncio.create_task(self._run_move(action))` — in test context this runs in the same event loop. Tests that call `manual_move` synchronously may need `await asyncio.sleep(0)` to let the task start.
