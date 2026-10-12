# Phase 4 — Async Tasks (main.py)

## Goal

Async tasks around the sync `CoopController`: control loop (tick, WDT, idle sleep), button tasks, LED task, AP session, crash policy. Single owner of the `main.py` spec (R27, §11). Implements state.py changes from decisions 1, 11, 12 and round-2/3 resolutions R1-R40 (`docs/decisions.md`). All testable on CPython with fakes.

Lightsleep is OFF in this phase (R25). The code path exists but is gated by `LIGHTSLEEP_OK = False`.

## Prerequisites (done, phases 1-3)

- `src/state.py`: `GateState`, `MovementTrigger`, `CoopController(motor, rtc, limit_top, limit_bottom, nsleep, nfault, config)`, `tick()`, `desired(now)`, `status_json()`, `get_forecast(days)`, `_start_move`, `_run_move`, `_on_limit_reached`, `_timed_out`, `_halt`, `_i2c_call`.
- `src/config.py`: `DEFAULTS`, `validate_config()` (raises `ConfigError`), `load()` (never raises), `save()` (atomic), `schedule(cfg, y, m, d)`.
- `src/astro.py`, `src/compat.py` (`ticks_ms`, `ticks_diff`, `sleep_ms`). Unchanged in this phase.
- `tests/mock_hardware.py`: `MockPin`, `MockMotor`, `MockRTC` (`datetime()` 7-tuple, R22), `make_ctrl()`.
- Flat imports everywhere (`from state import ...`). MicroPython-safe: no enum, dataclasses, contextlib, typing at runtime, get_running_loop, copy.
- Test runner: pytest-asyncio, `asyncio_mode = "auto"` (already in pyproject, R40).

## Deliverables

| File | Status | Content |
|------|--------|---------|
| `src/state.py` | extend | `manual_move(action, source)` (R1), `rtc_synced` (None/True/False, R31), `_last_local`, `osf` read (R17, R32), `_stop_motor()` (R19), reversal dead-time in `_run_move` (decision 11), INIT 2-read for all outcomes (R37), hooks `on_event`, `on_day_change` with guards (R4, R5, R29), `enter_error()` (R12), tick reads clock in SAFETY_STOP/ERROR (R30). state.py never imports logs. |
| `src/config.py` | extend | `"wifi": {"ap_password": ""}` in DEFAULTS. Validation: `""` OR str of 8..63 chars (R6). |
| `src/compat.py` | unchanged | Time shims only. No machine-access shims (R26). |
| `src/main.py` | create | Tasks, AP session, crash policy, module globals `ap_request`, `ap_active`, `ap_auto_done`, `LIGHTSLEEP_OK`. Device wiring under `if __name__ == "__main__":` (§11). |
| `tests/mock_hardware.py` | extend | `MockPin` for buttons/nSLEEP/nFAULT. `MockRTC.osf` flag + `osf()` (R32, added BEFORE state.py uses it). `FakeMachine` (`lightsleep`, `freq`, `reset`, records calls). `FakeWdt` (`feed` counter). |
| `tests/test_async.py` | create | 41 tests (§Tests). |
| `tests/test_hardware.py` | extend (owner 08) | Test 24 below only. |

No `src/web.py` here (phase 5). AP session gets `app_factory(ctrl, touch_cb)` via DI; phase-4 tests pass a fake app.

## TDD Protocol

1. Extend `tests/mock_hardware.py` (`osf` first). Write `tests/test_async.py`. Run `uv run pytest tests/test_async.py -v`. All new tests FAIL (assertion or ImportError). Record red output.
2. Implement state.py, config.py, then main.py. Re-run until green.

---

## Design

### 1. Time and machine access

- Time: `compat.ticks_ms`, `ticks_diff`, `sleep_ms` (unchanged). Tests patch `state.ticks_ms`, `state.sleep_ms`, `main.ticks_ms`, `main.sleep_ms` (names bound by value at import).
- Machine: one injected object `mch` with `lightsleep(ms)`, `freq()` (returns Hz), `freq(hz)`, `reset()`. Real = `machine` module on device. Tests = `FakeMachine`. Passed to `control_loop` and `ap_session`. No shim module.
- Wake arming for lightsleep: exact call TBD. See 08 V3. Not written until V3 passes.
- WDT: `machine.WDT(timeout=30_000)` created in `__main__` block, after hardware init. Fed in `control_loop` every iteration and before each lightsleep. 08 V2.

### 2. `control_loop(ctrl, wdt, btns, mch)` (R13, R25, R31)

Every iteration:
1. `wdt.feed()`.
2. `ctrl.tick()` (sync).
3. If `not ap_auto_done and ctrl.rtc_synced is False`: `ap_request = True`, `ap_auto_done = True` (once per boot, R8). `None` (never read) does not trigger (R31).
4. If `_can_lightsleep(ctrl, btns)`: `wdt.feed()`, `mch.lightsleep(2000)`, `await asyncio.sleep(0)` (R25: other tasks must run). Else `await sleep_ms(2000)`.

```python
from compat import sleep_ms
from state import GateState

IDLE_STATES = (GateState.IDLE_OPEN, GateState.IDLE_CLOSED,
               GateState.MANUAL_HOLD_OPEN, GateState.MANUAL_HOLD_CLOSED)
LIGHTSLEEP_OK = False  # True only after 08 V3 + V4 pass on device (R25)

def _can_lightsleep(ctrl, btns):
    return (LIGHTSLEEP_OK
            and ctrl.state in IDLE_STATES
            and ctrl.rtc_synced is True
            and not ap_active
            and not ap_request
            and btns[0].value() == 1 and btns[1].value() == 1)
```

- Lightsleep states exclude INIT, MOVING_*, SAFETY_STOP, ERROR: LEDs must blink (R14).
- Known limit: a tap whose press and release both fall inside one sleep is lost. 08 V4.
- `control_loop` never creates `_run_move`. Moves start only in `_start_move` (single owner). Test 3 enforces.

### 3. `button_task(ctrl, pin, other, action)` (R7, R34)

Two instances: `(btn_open, btn_close, "open")`, `(btn_close, btn_open, "close")`. Raw `machine.Pin` or `MockPin`. Active-low. Poll 20 ms.

Rules:
- Press AND release debounced 50 ms (`_stable`). Release debounce also covers the other button (R34).
- Release action only if press < 1000 ms AND other button was not held (debounced) during it → `ctrl.manual_move(action, "button")`.
- Both held >= 3000 ms (from debounced both-low) → `ap_request = True`. Both release events suppressed (no door move).
- Failed release-stable check keeps original `t0` / `both_t0` (bounced long press = no action, R34).

```python
from compat import sleep_ms, ticks_ms, ticks_diff

async def _stable(pin, level, ms):
    t = ticks_ms()
    while ticks_diff(ticks_ms(), t) < ms:
        if pin.value() != level:
            return False
        await sleep_ms(10)
    return True

async def button_task(ctrl, pin, other, action):
    global ap_request
    while True:
        if pin.value() != 0 or not await _stable(pin, 0, 50):
            await sleep_ms(20)
            continue
        t0 = ticks_ms()        # kept across failed release checks (R34)
        both_t0 = None
        while True:
            now = ticks_ms()
            if both_t0 is None and other.value() == 0 and await _stable(other, 0, 50):
                both_t0 = now
            if both_t0 is not None and ticks_diff(now, both_t0) >= 3000:
                ap_request = True
            if pin.value() == 1:
                rel = ticks_ms()
                if await _stable(pin, 1, 50):
                    break
            await sleep_ms(10)
        if both_t0 is None and ticks_diff(rel, t0) < 1000:
            ctrl.manual_move(action, "button")
```

- Action fires on RELEASE, never on press.
- `ap_request` stays pending while MOVING_* (§10 step 1).

### 4. `led_task(ctrl, red, green, yellow)` (R28, R23)

- One API for all three: `led.value(v)`, `v=1` = LED on. `red` = `Pin` (GPIO6, direct). `green`/`yellow` = `PcfLed` (active-low, PCF P0/P1). 08 must rename `PcfLed.set` → `value`.
- Each LED write wrapped `try/except OSError`: failed PCF write skips that LED, loop continues (test 14).
- Half-period toggle counter (not `tick % 2`). Loop sleep 125 ms in ERROR, 500 ms otherwise.

| Condition | Green | Red | Yellow |
|-----------|-------|-----|--------|
| `INIT` | off | off | off (R23) |
| `IDLE_OPEN`, `MANUAL_HOLD_OPEN` | solid | off | overlay |
| `IDLE_CLOSED`, `MANUAL_HOLD_CLOSED` | off | solid | overlay |
| `MOVING_OPEN`, `MOVING_CLOSE` | 1 Hz | off | overlay |
| `SAFETY_STOP` | off | 1 Hz | overlay |
| `ERROR` (incl. boot failure, R18) | off | 4 Hz | overlay |
| overlay: `rtc_synced is not True` or OSF | | | 1 Hz, any state except INIT |

Overlay "rtc_synced is not True" covers both False and None (after boot, None only before first read).

### 5. `manual_move(action, source)` (R1, R36, R37)

- Signature `manual_move(self, action: str, source: str) -> bool`. `source` required, `"button"` or `"web"`.
- Returns True if move started or reversal accepted. False = refused or no-op.
- Order of checks:
  1. `ERROR` → False (both sources).
  2. `source == "web"` and `SAFETY_STOP` → False.
  3. Same direction as current state (IDLE_*, MANUAL_HOLD_*, MOVING_* with same action) → False, no `_start_move`.
  4. Read clock (`_hold_desired = desired(now)`), re-check ERROR (I2C may ERROR).
  5. Trigger: `SAFETY_STOP` → RECOVERY, otherwise MANUAL. `INIT` → MANUAL (R37, documented, test 17).
  6. `_start_move(dir, trigger, reverse = state in MOVING_*)`. True.
- Web ignores the bool (R1). Web route (05) returns 409 in SAFETY_STOP/ERROR.
- No `motor.stop()` in `manual_move`. Reversal stop sequence is in `_run_move` (§6). Current line `self.motor.stop()` removed.

### 6. Stop order, reversal, PCF failures (R19, R36, decision 11)

Stop sequence (`_stop_motor`), used by `_halt`, limit stop and reversal:
1. `nsleep.value(0)` first (GPIO kill switch, works with dead I2C).
2. `motor.stop()` (IN1=IN2=LOW via PCF, coast), best effort, `OSError` swallowed.

- `_start_move(direction, trigger, reverse=False)`: set state, `_move_start_ms`, `_move_gen += 1`, create `_run_move(direction, gen, reverse)`.
- `_run_move(direction, gen, reverse)`:
  - `if gen != _move_gen: return`.
  - `reverse=True`: `_stop_motor()`, `await sleep_ms(200)`, `if gen != _move_gen: return`.
  - `nsleep.value(1)`, `await sleep_ms(1)`, check gen again.
  - Drive: `motor.forward()` / `motor.backward()`. `OSError` (PCF unreachable, reversal or fresh start) → `_halt(ERROR)`, return (R36).
  - Loop while gen matches and limit not hit: nFAULT low or timeout → `_halt(SAFETY_STOP)`, return.
  - Limit hit: `_stop_motor()`, `_on_limit_reached()`.
- Timeout clock restarts at reversal (`_move_start_ms` set in `_start_move`).
- PCF cache rule (R36): `Motor.stop()` clears IN1/IN2 bits in the PCF cache BEFORE the write attempt. A later LED write then never re-asserts IN1/IN2. Lives in 08 §2 `Motor`/`PCF8574`. Test 24.
- Device checks: back-EMF nFAULT 08 V20; LED pin hold during lightsleep 08 V21.

### 7. INIT, 2 consistent reads for every outcome (R9, R37, decision 12)

- `_init_sample` starts `None`. Each INIT tick: `sample = (limit_top == 0, limit_bottom == 0)`.
- If `sample != _init_sample`: store it, return. No decision on the first read.
- If equal (2nd consistent tick, ~2 s): decide.
  - both → `_halt(ERROR)`.
  - top only → IDLE_OPEN; bottom only → IDLE_CLOSED. Continue into normal tick.
  - neither → clock read; `_start_move("open" if want == "open" else "close", AUTO)` (homing; unknown clock → close, R9).
- Manual move during INIT allowed (§5), so INIT ends before any decision.

### 8. Hooks (R4, R5, R29, R30)

- `on_day_change(prev, new)` default no-op. Called from `_read_clock` on local date change when `prev` is not None. `prev` = `_last_local[:3]` BEFORE overwrite, never `_date` (R30). Not called on first sync (`_last_local` None). Known limit: no call across an unsynced gap (ring keeps events, flushed at next change).
- `on_event(kind, trigger)` default no-op. kinds: `open`, `close` (from `_on_limit_reached`), `safety_stop`, `error` (from `_halt`). Trigger: `MovementTrigger` int. ERROR trigger = AUTO (R29).
- Both wrapped:
```python
def _emit(self, kind, trigger):
    try:
        self.on_event(kind, trigger)
    except Exception as e:
        print("on_event:", e)
```
  Same shape for `_day_hook`. Tick never dies from a hook (R29).
- ERROR-once guard in `_halt`: `on_event("error")` fires only when `state` changes TO ERROR (`was != ERROR`). Repeated `_halt(ERROR)` silent.
- `_last_local = (y, mo, d, minute)` set on every successful synced read, `None` when unsynced. Logging reads `_last_local`, never `_date` (POST config/time invalidates `_date`).
- Flush: main binding (§11) calls `logs.flush()` on kind `error` and before `/api/reboot` reset. Log format and API: 06.

### 9. RTC: `rtc_synced`, OSF, `_read_clock`, `status_json` (R17, R30, R31, R32)

- `rtc_synced` starts `None`. `_read_clock` sets `True` (synced), `False` (year < 2020 or OSF), leaves it unchanged on I2C failure.
- `_read_clock`:
  1. `r = _i2c_call(rtc.datetime)`; None → return None.
  2. `osf = _i2c_call(rtc.osf)`; None → return None (read via `_i2c_call`, R32).
  3. `y < 2020 or osf` → `rtc_synced=False`, `_last_local=None`, `today_schedule=None`, `_date=None`, return `(False, 0)`.
  4. CEST adjust (`minute >= 1440` → next local day). Compute `prev` from `_last_local`, set `_last_local`, fire `_day_hook` on change, recompute schedule on date change.
- `tick()` in SAFETY_STOP and ERROR calls `_read_clock()` and never moves (R30). Day change, `rtc_synced`, `_last_local` stay current.
- `status_json()` uses cached `rtc_synced is True` and `_last_local` for `time` (`HH:MM`, None when unsynced). No RTC I/O (R32).
- OSF semantics: `rtc.osf()` True = untrusted = unsynced. Phase 8 `set_datetime` clears it.

### 10. AP session `ap_session(ctrl, app_factory, wifi, mch, cfg)` (decisions 1, 3, 8; R6, R8, R10, R16, R23, R35)

Long-lived task, polls every 500 ms. `wifi` DI object: `ap_on(ssid, password)`, `ap_off()`. Device impl in phase 8 (`network.WLAN(AP_IF)`).

Poll step, in order:
1. `not ap_request` → continue.
2. `ap_active` → `ap_request = False`, continue (no double start).
3. `ctrl.state` in MOVING_* → keep request pending, continue (decision 3).
4. Start:
   - `ap_request = False`, `ap_active = True`.
   - No freq call: CPU fixed 80 MHz from boot.py (08 V14: WiFi at 80 MHz).
   - `wifi.ap_on("Szczesliwy_Kurczak", cfg["wifi"]["ap_password"])`. `""` = open AP (R6).
   - WebREPL: if `cfg["wifi"]["webrepl_password"]` non-empty → lazy `import webrepl`, `webrepl.start(password=...)`. Start error printed, AP stays up.
   - `http["last"] = ticks_ms()` BEFORE the server await (R35).
   - `app = app_factory(ctrl, touch)`; `srv` = server task (`app.start_server(port=80)`, 08 V15 start_server port, 05). `touch()` sets `http["last"]` (before_request hook, R10).
5. Inline watchdog in the SAME poll loop (no separate task, R35): while server not exited, every 500 ms: `ticks_diff(ticks_ms(), http["last"]) > 600_000` → break (10 min idle).
6. `finally:` (runs on normal exit, idle, exception, cancel). Each step best-effort, fixed order: `app.shutdown()` (idempotent guard), `dns_task` cancel, `webrepl.stop()` (only if started, best-effort), `wifi.ap_off()`, `ap_active = False`.

- AP does NOT block door buttons.
- AP allowed in SAFETY_STOP and ERROR. Does NOT clear SAFETY_STOP (R23).
- Auto AP: §2 step 3 (RTC unsynced) and §11 boot ERROR. Once per boot via `ap_auto_done`. Same 10 min idle.
- Password empty = open AP. UI banner / write-only field: phase 5/7 (R6, R39).

### 11. `main.py` (R26, R27, R31, R40)

Single owner of this section (R27). 08 references it.

```python
import asyncio
import machine
from machine import Pin
from compat import sleep_ms
from config import load
from state import CoopController
import hardware
from hardware import Motor, PcfLed, RTC   # 08 classes; no factory, no module boot flag
import logs                                        # 06

LIGHTSLEEP_OK = False
ap_request = False     # SAFETY: flags are module globals (R26), no holder class
ap_active = False
ap_auto_done = False

async def supervised(name, coro):                  # R27 crash policy
    try:
        await coro
    except Exception as e:
        print(name, "crashed:", e)
        try:
            logs.flush()
        except Exception:
            pass
        machine.reset()

def boot_checks(ctrl, boot_ok):                    # R18, R31, R12
    global ap_request, ap_auto_done
    if not boot_ok:
        ctrl.enter_error()                         # public; never _halt from main
        ap_request = True                          # auto AP on boot ERROR (visible without cable)
        ap_auto_done = True

async def run(ctrl, wdt, btns, leds, mch, wifi, app_factory, cfg):
    t = asyncio.create_task
    t(supervised("control_loop", control_loop(ctrl, wdt, btns, mch)))
    t(supervised("button_open", button_task(ctrl, btns[0], btns[1], "open")))
    t(supervised("button_close", button_task(ctrl, btns[1], btns[0], "close")))
    t(supervised("led", led_task(ctrl, leds[0], leds[1], leds[2])))
    t(supervised("ap", ap_session(ctrl, app_factory, wifi, mch, cfg)))
    await asyncio.Event().wait()                   # one await, run forever

def on_event_log(kind, trigger):
    ll = ctrl._last_local
    if ll is not None:
        logs.record(logs.days_since_2025(*ll[:3]), ll[3], logs.KIND_BY_NAME[kind], trigger)              # record: 06 §4
    if kind == "error":
        logs.flush()                               # R29: flush on entering ERROR

def on_day_flush(prev, new):
    logs.flush()

if __name__ == "__main__":                         # 08 V22: MicroPython __name__ for main.py
    i2c = hardware.get_i2c()                       # shared singleton (08 §2)
    found = i2c.scan()                             # main scans I2C itself (R26, R40)
    if not ({0x20, 0x68} <= set(found)):
        hardware.recover_bus()                     # 08 §2: 9 SCL clocks, resets singletons
        i2c = hardware.get_i2c()
        # then rescan
        found = i2c.scan()
    boot_ok = {0x20, 0x68} <= set(found)
    pcf = hardware.get_pcf()                       # singleton: boot.py 0xF3 cache shared
    try:
        pcf.write(0xF3)                            # coast, LEDs off (08 V9)
    except OSError:
        boot_ok = False
    cfg = load("/config.json")
    ctrl = CoopController(Motor(pcf, in1=2, in2=3), RTC(i2c),
                          Pin(0, Pin.IN, Pin.PULL_UP), Pin(1, Pin.IN, Pin.PULL_UP),
                          Pin(21, Pin.OUT), Pin(20, Pin.IN), cfg)   # nSLEEP LOW from boot.py (08 V13)
    ctrl.on_event = on_event_log                   # bound before boot checks so the ERROR flush runs; boot ERROR is not recorded (_last_local None, 06 §5)
    ctrl.on_day_change = on_day_flush
    boot_checks(ctrl, boot_ok)
    wdt = machine.WDT(timeout=30_000)
    btns = (Pin(7, Pin.IN, Pin.PULL_UP), Pin(10, Pin.IN, Pin.PULL_UP))
    leds = (Pin(6, Pin.OUT), PcfLed(pcf, 0), PcfLed(pcf, 1))   # red, green, yellow
    asyncio.run(run(ctrl, wdt, btns, leds, machine, wifi, app_factory, cfg))  # wifi, app_factory: phase 8 / 5
```

- `ctrl.on_event` bound before `boot_checks` so the ERROR flush runs; boot ERROR is not recorded (_last_local None, 06 §5).
- `on_event` binding: on kind `"error"` → `logs.flush()` (R29). Record details: 06.
- Crash policy (R27): each task body wrapped in `supervised`. Exception → print, best-effort flush, `machine.reset()`.
- `Pin(21, Pin.OUT)` constructed without value keeps LOW from boot.py (08 V13).
- Watchdog/freq/AP: §1, §10.
- `wifi`, `app_factory`: phase 8 and phase 5. Phase-4 tests inject fakes.

---

## Tests (`tests/test_async.py` unless noted) — write first, all must fail now

FakeClock fixture patches `state.ticks_ms`, `state.sleep_ms`, `main.ticks_ms`, `main.sleep_ms`. No real sleeps > 50 ms. `FakeMachine` and `FakeWdt` from `tests/mock_hardware.py`. Module flags set via monkeypatch (`main.LIGHTSLEEP_OK`, `main.ap_request`, ...).

"Red" = why it fails now. "Guard" = behaviour already correct; locks it.

### control_loop
1. `test_loop_ticks_and_feeds_wdt_each_iteration` — MOVING, 6 s fake → 3 ticks, 3 feeds. Red: control_loop missing.
2. `test_lightsleep_only_when_every_gate_passes` — parametrized: all gates true → `lightsleep(2000)` then `asyncio.sleep(0)`; each negative (MOVING_OPEN, INIT, SAFETY_STOP, ERROR, `ap_active`, `ap_request`, button low, `rtc_synced` None or False, `LIGHTSLEEP_OK` False) → no lightsleep, `sleep_ms(2000)` used. Red: `_can_lightsleep` missing.
3. `test_loop_never_creates_run_move` — `asyncio.create_task` recorder over 3 iterations, IDLE_CLOSED with 09:00 desired open. Only `_start_move` creates `_run_move`. Red: control_loop missing.
4. `test_auto_ap_once_per_boot_on_unsynced_or_osf` — parametrized (`rtc_synced` False; OSF at boot): `ap_request` True once; after AP ends, no second request. Red: no `ap_auto_done`.
5. `test_auto_ap_not_requested_when_rtc_never_read` — `rtc_synced` None → `ap_request` stays False (R31). Guard.
6. `test_tick_reads_clock_in_safety_stop_and_error` — SAFETY_STOP and ERROR: `tick()` updates `rtc_synced`, `_last_local`, fires day hook on rollover; state unchanged (R30). Red: tick returns early.

### button_task
7. `test_release_under_1s_calls_manual_move_on_release` — press 300 ms: no call at 200 ms; release → `manual_move("open", "button")` once. Red: source param, release logic.
8. `test_hold_1s_or_longer_no_action_incl_bounced_release` — 1200 ms press; bounce on release (R34) → no `manual_move`. Red: no R34 logic.
9. `test_debounce_ignores_glitch_and_release_bounce` — 20 ms low pulse → no call; release bounce 20 ms high → exactly one call at final release. Red: no debounce.
10. `test_both_held_3s_sets_ap_request_no_move` — both 3000 ms → `ap_request` True, no `manual_move` on either release; both 2000 ms → `ap_request` False. Red: no `ap_request` global.

### led_task
11. `test_led_steady_states_and_init_all_off` — IDLE_OPEN: green solid, red off; IDLE_CLOSED: red solid, green off; INIT: all off (R23). Red: no led_task.
12. `test_led_blink_rates` — parametrized: MOVING green 500 ms toggle; SAFETY_STOP red 500 ms toggle; ERROR red 125 ms toggle. Red: no led_task.
13. `test_led_yellow_overlay_unsynced_or_osf` — parametrized (rtc_synced False, OSF): yellow 500 ms toggle, green still solid; INIT excluded. Red: no led_task.
14. `test_led_pcf_oserror_keeps_loop_alive` — PCF `value` raises OSError → loop continues, red still updated. Red: no led_task.

### manual_move / state.py
15. `test_manual_web_refused_in_safety_stop_button_recovers` — web in SAFETY_STOP → False, state unchanged; button → MOVING_OPEN, trigger RECOVERY. Red: no source param.
16. `test_manual_error_refused_both_sources` — ERROR → False for button and web. Red: no source param.
17. `test_manual_same_direction_noop_init_allowed_source_required` — IDLE_OPEN + open → False, no `_start_move`; INIT + open → MOVING_OPEN MANUAL (R37); call without source → TypeError. Red: source param.
18. `test_reversal_dead_time_and_stop_order` — MOVING_OPEN, manual close at t=0: nSLEEP LOW and IN1=IN2 LOW at t=0; `backward` not before 200 ms; nSLEEP HIGH only after dead time; `motor.stop` count 0 at `_start_move` entry (R19, decision 11). Red: `motor.stop` in manual_move, no dead time.
19. `test_reversal_supersedes_old_run_move` — old `_run_move` (open) exits on gen mismatch, no `forward` after reversal, state not overwritten. Guard.
20. `test_reversal_pcf_oserror_goes_error` — `backward` raises OSError on reversal → state ERROR, nSLEEP LOW (R36). Red: no reversal drive guard.
21. `test_stop_order_nsleep_first_everywhere` — parametrized (`_halt`, limit stop, reversal): nSLEEP LOW recorded before `motor.stop`. Halt and limit: guard. Reversal: red.
22. `test_halt_kills_running_run_move` — `_halt` during `_run_move` poll → run_move exits, state stays SAFETY_STOP. Guard.
23. `test_halt_error_after_repeated_i2c_fail` — 3 consecutive `datetime` OSErrors in tick → ERROR; `on_event("error")` once. Guard.
24. `test_pcf_motor_stop_clears_cache_before_write` — FILE `tests/test_hardware.py` (08 Motor). PCF write raises OSError on `stop()`; next `PcfLed.value()` write must not contain IN1/IN2 bits (R36). Red: 08 Motor caches after write.

### INIT
25. `test_init_two_consistent_reads_all_outcomes` — parametrized (top only, bottom only, both → ERROR, neither+synced → homing open/close per want, neither+unsynced → close): 1 read → state INIT; 2 consistent reads → decision (R37, R9). Red: current code decides on first read.
26. `test_init_glitch_then_disagree_resets` — top=0 then bottom=0 → still INIT, no move; then two equal reads decide. Red: single-read decision.

### OSF / unsynced
27. `test_osf_treated_as_unsynced` — `rtc.osf()` True with 09:00 IDLE_CLOSED → `today_schedule` None, `desired()` None, no auto move. Red: no `osf()` call.
28. `test_osf_clear_restores_auto_move` — OSF False → schedule set, auto open starts. Red: no osf read.
29. `test_hold_expires_on_first_valid_desired_when_pressed_unsynced` — MANUAL_HOLD_OPEN pressed unsynced (`_hold_desired` None); first valid desired → hold expires to IDLE_* (R38, intended). Guard.

### hooks
30. `test_day_change_hook_once_prev_from_last_local` — 2024-06-01 → 06-02 over 2 ticks: hook once with both dates; first sync: no hook; `_date` set None then rollover: hook still fires with prev from `_last_local` (R4, R30). Red: no hook / `_last_local`.
31. `test_hook_exceptions_never_kill_tick` — `on_event` and `on_day_change` raise Exception → tick returns, state preserved (R29). Red: no try/except.
32. `test_on_event_kinds_and_last_local_fields` — limit reached → `("open"|"close", trigger)`; halt SAFETY_STOP → `"safety_stop"`; `_date` None, event still carries date/minute from `_last_local` (R5). Red: no on_event.
33. `test_error_event_fires_once_and_main_flushes` — `_halt(ERROR)` twice → on_event `"error"` once, trigger AUTO; `main` binding calls `logs.flush` on `"error"` (spy) (R29). Red: no guard.

### AP session
34. `test_ap_teardown_on_exception_and_cancel` — parametrized (`start_server` raises RuntimeError; task cancelled while serving): `wifi.ap_off` called, no `mch.freq` call, `ap_active` False, `app.shutdown` called once. Red: no ap_session.
35. (removed) freq-restore test: no bump anymore (80 MHz fixed); #34 asserts no freq call.
36. `test_ap_idle_timeout_and_http_touch_reset` — no touch for 10 min + 60 s → shutdown + teardown; touch every 5 min → no shutdown after 30 min (R10, R35). Red: no ap_session.
37. `test_ap_deferred_while_moving_then_starts` — MOVING_CLOSE + request → `ap_on` not called, request stays True; state IDLE_OPEN → starts within one 500 ms poll (decision 3). Red: no ap_session.
38. `test_ap_allowed_in_safety_stop_does_not_clear` — SAFETY_STOP + request → `ap_on` called, state still SAFETY_STOP (R23). Red: no ap_session.
39. `test_ap_on_receives_config_password_incl_empty` — `cfg["wifi"]["ap_password"]` `""` → `ap_on(…, "")`; `"abcdefgh"` → passed through (R6). Red: no ap_session.
40. `test_ap_password_validation` — parametrized: `""` OK, 8 chars OK, 63 chars OK; 7 chars, 64 chars, non-str → `ConfigError`. File: `tests/test_async.py` (R6, R40). Red: no wifi key.

### boot / crash policy
41. `test_boot_not_ok_enters_error_without_halt_from_main` — `boot_checks(ctrl, False)`: state ERROR, `ap_request` True, `ap_auto_done` True, `_halt` spy not called from main path (R12, R18, R31). Red: no boot_checks.
42. `test_task_crash_resets_via_supervised` — coroutine raises RuntimeError under `supervised` → print, flush attempt, `FakeMachine.reset` called once (R27). Red: no supervised.

Count: 42 numbered. 41 in `tests/test_async.py`, #24 in `tests/test_hardware.py`. Within the 35-45 target. Dropped vs old 72: source-grep tests, duplicates of test_state/test_config, machine-shim tests (gone with the shim module), holder-class tests (folded into 4 and 35), per-LED duplicates (folded into 11-13).

---

## Verification (CPython)

```bash
uv run pytest tests/test_async.py -v          # all green after implementation
uv run pytest tests/test_hardware.py -v       # test 24 (08 Motor cache)
uv run pytest tests/ -v                       # no regression in phases 1-3
uv run ruff check src/state.py src/main.py src/config.py tests/test_async.py
uv run ruff format --check src/ tests/
```

Device checks: none new in 04. Use the single checklist in 08 §5 (tap, reversal, AP, OSF, lightsleep current). Lightsleep rows apply only after V3 + V4.

## VERIFY ON DEVICE (references to 08 V-table, no own numbering)

- 08 V1 dropped (CPU fixed 80 MHz; no 40 MHz try).
- 08 V2 — WDT across `lightsleep`; feed-before-sleep (§2, §1).
- 08 V3 — BLOCKING. Wake on EITHER button. `LIGHTSLEEP_OK` stays False until pass (§2, R25).
- 08 V4 — wake latency vs release-before-CPU (tap loss, §2 known limit). Gate for `LIGHTSLEEP_OK`.
- 08 V5 — `ticks_ms` continuity across lightsleep (§2).
- 08 V9 — PCF power-on read-back and first write 0xF3 (§11).
- 08 V13 — `Pin(21, Pin.OUT)` keeps LOW (§11).
- 08 V12 — settle time before first PCF write (§11).
- 08 §5.6 — reversal gap ≥ 200 ms measured (§6). 08 §5.6 checklist.
- 08 §5.9 — current draw, lightsleep rows (§2). 08 §5.9 checklist.

## Device refs (V-ids owned by 08, no own list)

- 08 V20: back-EMF nFAULT on reversal/coast (§6).
- 08 V21: LED pin hold during lightsleep (§2, §4). Blocks LIGHTSLEEP_OK with V3/V4.
- 08 V22: MicroPython runtime, `__name__`, `asyncio.run`, `Event().wait()`, `machine.reset()` (§11).
- 08 V15: `start_server` port / microdot API (§10). Routes: 05.
- 08 V14: WiFi at 80 MHz (§10).

## Open items

- Wake arming API (08 V3). Not written until verified.
- `wifi` DI device impl and `app_factory` wiring: phase 8 and phase 5.
- Known limit: no `on_day_change` across an unsynced gap (§8). Owner decide if flush-on-unsync needed.
- Known limit: tap lost during lightsleep (§2). 08 V4.
