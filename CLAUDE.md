# CLAUDE.md

## Project

Smart Coop V2: automatic chicken coop door. ESP32-C3 Super Mini + MicroPython + asyncio.
Schedule-only (no light sensor): open = sunrise + offset, close = civil dusk (sun -6°) + offset. NOAA formula, ±2 min.
DS3231 RTC, JGY-370 worm-gear motor (self-braking) via DRV8833. WiFi AP on demand.
`legacy/` = v1 (ATmega328P/Arduino/C++), archive only, latest state incl. GateLogic.cpp + tests.

## Status

| Phase | Deliverable | State |
|-------|-------------|-------|
| 1 | `src/compat.py`, `src/astro.py`, `tests/test_astro.py` | DONE |
| 2 | `src/config.py`, `config.default.json`, `tests/test_config.py` | DONE |
| 3 | `src/state.py`, `tests/mock_hardware.py`, `tests/conftest.py`, `tests/test_state.py` | DONE |
| 4 | `src/main.py` (asyncio loops), `tests/test_async.py` | CODE (CPython tests) |
| 5 | `src/web.py` (microdot REST, no SSE), `run_local.py`, `tests/test_api.py` | CODE |
| 6 | `src/logs.py` (binary 4 B log), `tests/test_logs.py` | CODE |
| 7 | `src/www/index.html` (simple.css 2.3.7 inlined, gzip < 10 KB) | CODE, browser unchecked |
| 8 | `src/hardware.py`, `src/boot.py`, `tests/test_hardware.py` | CODE, device VERIFY V1-V24 open; AP + phone verified |

Naming/layout: `CONVENTIONS.md`.

Specs for 4-8: `docs/phases/*.md`; decisions: `docs/decisions.md` (R1-R42 binding).

## Dev Commands

```bash
uv sync --extra dev                 # required first, else pytest missing
uv run pytest tests/ -q
uv run pytest tests/test_state.py
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

## MicroPython Rules (src/ only)

- Banned: `enum`, `dataclasses`, `contextlib`, `typing` at runtime, `asyncio.get_running_loop`, `copy`.
- Imports FLAT + module-level: `from astro import ...`. Never `from src.X` (device FS is flat). Tests resolve via pytest `pythonpath = ["src", "."]`.
- Constants as plain classes (`GateState.IDLE_OPEN == "IDLE_OPEN"`). Config = plain dict.
- Annotations OK (ignored at runtime). ASCII strings in runtime messages.
- DI: `CoopController` takes hardware objects in ctor; tests pass mocks.
- Device runs `.mpy` only (`scripts/deploy.py`). Never upload src `.py` to the device: on-device compile grows the MP heap into IDF DRAM → WiFi OOM (measured IDF free 3.6 KB vs 104 KB). Exceptions: `boot.py` (MP runs it only as `.py`) and the 2-line device `main.py` (`import coop`; `coop.start()`). New module → must land in `deploy.py` `MODULES`.

## Key Files

| File | Role |
|------|------|
| `src/compat.py` | `ticks_ms`, `ticks_diff`, `sleep_ms` shims (CPython = monotonic) |
| `src/astro.py` | `sun_times_cet(y,m,d,lat,lon,alt_deg=-0.833)`, `is_dst`, `local_minutes`, `next_day`, `days_in_month`, `day_of_year` |
| `src/config.py` | `DEFAULTS`, `load` (never raises → defaults), `save` (validate + atomic tmp/rename), `validate_config`, `schedule(cfg,y,m,d) -> (open_min, close_min)` local |
| `src/state.py` | `GateState`, `MovementTrigger` (ints), `CoopController` (`manual_move(action, source)`, `forecast`, hooks `on_event`/`on_day_change`) |
| `src/main.py` | `control_loop`, `button_task`, `led_task`, `ap_session`, `supervised` (task crash → reset) |
| `src/web.py` | microdot `create_app(ctrl, touch_cb, ...)` |
| `src/dns.py` | Captive-portal DNS: `build_reply` (every A query → 192.168.4.1), `serve_dns`; runs only while AP up |
| `src/logs.py` | binary log: `record`, `flush`, `read_tail`, `log_json` |
| `src/hardware.py` | `get_i2c`, `recover_bus`, `PCF8574`, `PcfLed`, `Motor`, `RTC` (DS3231, `osf`), `ap_on`/`ap_off` |
| `src/boot.py` | nSLEEP LOW first, PCF 0xF3, `logs.init_log` |
| `run_local.py` | dev server on mocks, 127.0.0.1:5000 |
| `scripts/deploy.py` | Build + upload: src → `.mpy` (mpy-cross 1.29.0.post2), `main.py` → `coop.mpy`, boot.py, index.html.gz, device `main.py`; `--first` adds config.json |
| `tests/mock_hardware.py` | `MockPin`, `MockMotor`, `MockRTC`, `make_ctrl()` |
| `tests/conftest.py` | fixture `ctrl`: IDLE_CLOSED, schedule patched to (360, 1080), RTC 2026-06-15 12:00 CET |

## Config Schema

```json
{
  "location": {"lat": 50.01, "lon": 20.99},
  "schedule": {"open_after_sunrise_min": 30, "close_after_dusk_min": 0, "open_not_before_min": null, "close_not_after_min": null},
  "safety":   {"move_timeout_s": 21}
}
```
Validation: exact keys, no bool-as-int, lat [-90,90], lon [-180,180], offsets int [-120,180], timeout int [5,120], clamps `null` or int [0,1439].
Clamps (optional, default null): `open = max(open, open_not_before_min)`, `close = min(close, close_not_after_min)`, applied after DST + offsets. Open >= close → `config_warning` (state.py, unchanged).
`load` merges file over defaults, ignores unknown keys (old v2 configs load fine), any error → defaults.

## State Machine

```
GateState: INIT, IDLE_OPEN, IDLE_CLOSED, MOVING_OPEN, MOVING_CLOSE,
           MANUAL_HOLD_OPEN, MANUAL_HOLD_CLOSED, SAFETY_STOP, ERROR
MovementTrigger: AUTO | MANUAL | RECOVERY
```

- `tick()`: sync, every 2 s. Reads RTC only via `_i2c_call` (3 consecutive OSError → ERROR).
- desired = open if `open <= now_local < close` else closed. IDLE_* moves toward desired (AUTO).
- Local date rollover (incl. CEST 23:xx CET = next local day) → recompute `today_schedule`. Covers midnight, DST, post time-sync.
- RTC unsynced (year < 2020): no auto moves. INIT: top limit → IDLE_OPEN, bottom → IDLE_CLOSED, both → ERROR, neither → move to desired (unsynced: close).
- Manual hold: `manual_move` → MANUAL_HOLD_* after limit; lasts until desired changes vs value at press time, then IDLE_* (same position) and normal logic.
- Single move owner: `_start_move` (state, start time, `_move_gen += 1`, spawn `_run_move`). Both tick and manual_move use it. main.py must NOT spawn moves.
- `_run_move(direction, gen)`: exits silently if superseded (gen mismatch). Checks nFAULT + timeout in loop (tick checks timeout too, as backstop).
- `manual_move` works from any state except ERROR; reverses running move. From SAFETY_STOP = RECOVERY → ends IDLE_*, not hold.
- SAFETY_STOP: no auto recovery; cleared only by physical button (web refused, 409; phase 4 adds `source`). ERROR: reboot only.
- Day with open >= close → `config_warning`, no auto moves.

## Hardware Pins (ESP32-C3 Super Mini)

| Signal | GPIO | Note |
|--------|------|------|
| I2C SDA / SCL | 4 / 5 | DS3231 (0x68) + PCF8574 (0x20) |
| LIMIT_TOP / LIMIT_BOTTOM | 0 / 1 | INPUT_PULLUP, 0 = hit |
| VBAT_SENSE | 3 | unused (VBAT disconnected) |
| LED_RED | 6 | active-high, works without I2C |
| BTN_OPEN / BTN_CLOSE | 7 / 10 | INPUT_PULLUP |
| nFAULT / nSLEEP | 20 / 21 | DRV8833; nSLEEP 0 = sleep (~2 µA) |
| GPIO2/8/9 | — | strapping, do not use |

PCF8574: P0 LED_GREEN, P1 LED_YELLOW (active-low), P2/P3 motor IN1/IN2.
## Hardware Notes

- DS3231 ZS-042: **remove charging diode + resistor before CR2032** (fire hazard).
- DS3231 stores CET always; DST only in logic.
- nSLEEP LOW between moves; wake 1 ms before drive. Coast = IN1=IN2=LOW; worm gear holds.
- Motor direction change = one PCF8574 byte write (IN1/IN2 atomic), after ~200 ms coast on reversal.
- Obstruction detection = DRV8833 overcurrent (nFAULT) only.
- Power: VMOT direct from PSU, ESP32 via USB 5V, 3V3 → DS3231/PCF8574.

## Phases 4-8 design (implemented, device VERIFY open) — details in docs/phases/

- Deploy: `uv run python scripts/deploy.py COM<N> [--first]` (after `uv sync`; microdot from `.venv`). Compiles src modules + microdot to `.mpy` (`uvx --from mpy-cross==1.29.0.post2`), `main.py` → `coop.mpy`; deletes those `.py` on device; uploads `.mpy`; gzips `src/www/index.html` → `:/www/index.html.gz`; uploads `boot.py` as plain `.py`; writes device `main.py` = `import coop` / `coop.start()` last. `--first` also uploads `config.default.json` → `:config.json`; without it `:config.json` and `:logs.bin` are never touched.
- Buttons act on RELEASE (< 1 s). Both buttons held 3 s → AP `Szczesliwy_Kurczak` (password from config `wifi.ap_password`, `""` = open AP, not in repo). AP deferred while moving, 10 min idle off.
- SAFETY_STOP cleared only by physical button; `/api/manual` → 409 (web refused, R1). ERROR: reboot only.
- manual_move(action, source): source REQUIRED (button|web); allowed from INIT (MANUAL, R37).
- Power: lightsleep DEFERRED (R25) until V-wake (08 V3) passes; then `await asyncio.sleep(0)` after each. WDT 30 s, CPU fixed 80 MHz; earlier 80 MHz AP-connect panic was IDF heap OOM, fixed by .mpy deploy (R41) (boot.py; no runtime freq switching).
- RTC: DS3231 OSF flag = time untrusted → treated as unsynced. No VBAT ADC.
- Boot: nSLEEP LOW first line of boot.py, PCF8574 write 0xF3. I2C missing → ERROR, never a boot loop.
- Motor reversal: ~200 ms coast before opposite drive. INIT: 2 consistent reads, all outcomes (R37).
- Web: one `index.html`, inline CSS/JS, no CDN, polls `/api/status` every 3 s. /api/log = fixed newest 50, /api/forecast = fixed 14 days, no query params (R26). API: status, config, time, manual, forecast, log, reboot. No SSE, no WebREPL.
- Log: RAM ring + daily flush, binary 4 B/event, tail-only reads (R24, docs/phases/06).
- LEDs: green solid open, red solid closed, green 1 Hz moving, red 1 Hz SAFETY_STOP, red 4 Hz ERROR, yellow 1 Hz RTC unsynced/OSF.
- First boot: RTC unsynced → AP → sync time from phone.
