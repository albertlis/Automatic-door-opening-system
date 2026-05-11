# CLAUDE.md

## Project

Smart Coop V2 — automatic chicken coop door. ESP32-C3 Super Mini + MicroPython + asyncio.
Door open/close: BH1750 light sensor (5-sample unanimity) + DS3231 RTC time windows.
Drive: JGY-370 DC motor (self-braking worm gear) via DRV8833. WiFi AP on-demand.
Prev v1 (ATmega328P/Arduino/C++) — dead. This repo = v2.

## Dev Commands

```bash
uv sync --extra dev              # install deps (Python >=3.13)
uv run pytest tests/ -v          # all tests
uv run pytest tests/test_X.py    # single suite
uv run ruff check src/ tests/    # lint
uv run ruff format src/ tests/   # format
python run_local.py              # web UI at http://localhost:5000
```

## Flash / Upload (ESP32-C3)

```bash
# Flash MicroPython
esptool --chip esp32c3 --port COM<N> erase_flash
esptool --chip esp32c3 --port COM<N> --baud 460800 write_flash -z 0x0 ESP32_GENERIC_C3-*.bin

# Upload (order matters — main.py last)
mpremote connect COM<N> fs mkdir /www
mpremote connect COM<N> fs cp src/compat.py   :compat.py
mpremote connect COM<N> fs cp src/astro.py    :astro.py
mpremote connect COM<N> fs cp src/config.py   :config.py
mpremote connect COM<N> fs cp config.default.json :config.json
mpremote connect COM<N> fs cp src/logs.py     :logs.py
mpremote connect COM<N> fs cp src/state.py    :state.py
mpremote connect COM<N> fs cp src/hardware.py :hardware.py
mpremote connect COM<N> fs cp src/web.py      :web.py
mpremote connect COM<N> fs cp src/www/index.html  :/www/index.html
mpremote connect COM<N> fs cp src/www/config.html :/www/config.html
mpremote connect COM<N> fs cp src/www/logs.html   :/www/logs.html
mpremote connect COM<N> fs cp src/www/debug.html  :/www/debug.html
mpremote connect COM<N> fs cp src/boot.py     :boot.py
mpremote connect COM<N> fs cp src/main.py     :main.py   # last — triggers boot

# REPL
mpremote connect COM<N> repl
```

## Architecture

8 phases (TDD — tests first, all fail, implement until green):

| Phase | Deliverable |
|-------|-------------|
| 1 | `src/compat.py`, `src/astro.py`, `tests/test_astro.py` |
| 2 | `src/config.py`, `config.default.json`, `tests/test_config.py` |
| 3 | `src/state.py` (sync), `tests/mock_hardware.py`, `tests/conftest.py`, `tests/test_state.py` |
| 4 | `src/state.py` (async), `src/main.py`, `tests/test_async.py` |
| 5 | `src/web.py`, `run_local.py`, `tests/test_api.py` |
| 6 | `src/logs.py`, extend state+web, `tests/test_logs.py` |
| 7 | `src/www/*.html` (manual browser check only) |
| 8 | `src/hardware.py`, `src/boot.py` (physical ESP32 verify) |

DI everywhere: `CoopController` takes hardware objects in ctor → CPython tests use mocks.

## Key Files

| File | Role |
|------|------|
| `src/compat.py` | MicroPython/CPython shims: `ticks_ms`, `ticks_diff`, `sleep_ms` |
| `src/astro.py` | Pure math: `sun_times_cet`, `is_dst`, `local_minutes`, `day_of_year` |
| `src/config.py` | `Config` dataclasses, `load`/`save`/`validate_config`, helper fns |
| `src/state.py` | `GateState` enum, `MovementTrigger`, `CoopController` (tick + async) |
| `src/main.py` | asyncio entry: `control_loop`, `light_sensor_loop`, `led_loop`, `button_monitor`, `start_ap_session` |
| `src/web.py` | microdot REST + SSE + SVG forecast + static HTML serving |
| `src/logs.py` | Binary circular buffer 365×8B on LittleFS |
| `src/hardware.py` | MicroPython-only: `PCF8574`, `Motor`, `RTC`, `LightSensor`, `Button`, `LED`, `VBatADC` |
| `src/boot.py` | `freq(40MHz)`, WiFi off, I2C scan, `init_log()` |
| `tests/mock_hardware.py` | `MockMotor/RTC/LightSensor/Button/LED/PCF/NSleep/NFault` |
| `tests/conftest.py` | Fixtures: `ctrl` (legacy mode, fixed hours), `client` (microdot test client) |
| `config.default.json` | Default config shipped with firmware |
| `run_local.py` | Dev runner: mocks + microdot on localhost:5000 |

## State Machine

```
GateState: INIT → IDLE_OPEN / IDLE_CLOSED
                → MOVING_OPEN / MOVING_CLOSE
                → MANUAL_HOLD_OPEN / MANUAL_HOLD_CLOSED
                → SAFETY_STOP
                → ERROR

MovementTrigger: AUTO | MANUAL | RECOVERY
```

`tick()` — synchronous, called every 2s by `control_loop`. No asyncio inside.
`control_loop` spawns `asyncio.create_task(ctrl._run_move(...))` on state transition.
`manual_move()` — highest priority, works from any state, interrupts opposite movement.
`SAFETY_STOP` cleared only by physical button (RECOVERY trigger → after limit → IDLE_*, not MANUAL_HOLD_*).
`ERROR` cleared only by reboot.

Decision logic:
- `sensor_open`: all 5 lux > `lux_open` AND time in `[wo, wc)`
- `sensor_close`: all 5 lux < `lux_close` AND time outside `[wo, wc)`
- `abs_open` backstop: `ao <= now < wc` (inside window, forces open if sensor missed)
- `abs_close` backstop: `now >= ac OR now < wo` (night zone, forces close)

## Hardware Pins (ESP32-C3 Super Mini)

| Signal | GPIO | Note |
|--------|------|------|
| I2C SDA | 4 | BH1750 + DS3231 + PCF8574 |
| I2C SCL | 5 | |
| LIMIT_TOP | 0 | INPUT_PULLUP |
| LIMIT_BOTTOM | 1 | INPUT_PULLUP |
| VBAT_SENSE | 3 | ADC1_CH3, direct to DS3231 VBAT pin |
| LED_RED | 6 | OUTPUT, active-high, direct GPIO |
| BTN_OPEN | 7 | INPUT_PULLUP |
| BTN_CLOSE | 10 | INPUT_PULLUP |
| nFAULT (DRV8833) | 20 | INPUT, active-low |
| nSLEEP (DRV8833) | 21 | OUTPUT, 0=sleep (~2µA) |
| GPIO2/8/9 | — | Strapping pins — do not use |

PCF8574 (0x20): P0=LED_GREEN(active-low), P1=LED_YELLOW(active-low), P2=Motor IN1, P3=Motor IN2.
I2C bus: PCF8574=0x20, BH1750=0x23, DS3231=0x68.

## Hardware Notes

- DS3231 ZS-042: **remove charging diode + resistor before CR2032** — non-rechargeable battery + charging circuit = fire hazard.
- DS3231 stores CET (UTC+1) always. DST offset (+60 min) applied only in logic layer.
- DRV8833 nSLEEP=LOW between moves (~2µA). Wake 1ms before motor start.
- Motor coast: IN1=IN2=LOW. Worm gear self-brakes — no holding current needed.
- `motor.forward()`: set IN2=LOW first, then IN1=HIGH (avoids momentary brake).
- Power star topology: VMOT (DRV8833) direct from PSU; ESP32 via USB 5V; 3V3 pin → BH1750/DS3231/PCF8574.

## Config Schema

```json
{
  "window":        { "mode": "sun_position|legacy",
                     "legacy": {"hour_open":6, "hour_close":18},
                     "sun":    {"sunrise_offset_min":-30, "sunset_offset_min":30} },
  "override_open": { "mode": "dynamic|fixed",
                     "fixed_hour":8, "after_sunrise_min":120 },
  "override_close":{ "mode": "fixed|dynamic",
                     "fixed_hour":22, "after_sunset_min":120 },
  "light":         { "lux_open":8.0, "lux_close":3.0 },
  "safety":        { "move_timeout_s":21 }
}
```

Invariant (hard-validated on POST /api/config, soft-clamped at runtime):
`window_open <= abs_open < abs_close >= window_close`

## Web UI

AP on-demand: double-click BTN_OPEN or BTN_CLOSE → `Coop_Control` (pw: coop123).
Auto-off: 10 min idle. WiFi needs 80MHz — `start_ap_session` bumps freq, reverts after.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/status` | GET | state, lux[5], time, limits, vbat, config_warning, today_schedule |
| `/api/events` | GET | SSE stream, status JSON every 2s |
| `/api/config` | GET/POST | read/write config.json |
| `/api/time` | POST | `{timestamp_ms}` UTC→CET→DS3231 |
| `/api/manual` | POST | `?action=open\|close` |
| `/api/forecast` | GET | `?days=N` (max 90), 30-day default, [[wo,wc,ao,ac],...] |
| `/api/logs` | GET | binary log as JSON array |
| `/api/reboot` | POST | soft reset |

Pages: `/` status+SVG, `/config`, `/logs`, `/debug`.

## LED Signals

| State | LED | Pattern |
|-------|-----|---------|
| IDLE_OPEN / MANUAL_HOLD_OPEN | green | solid |
| IDLE_CLOSED / MANUAL_HOLD_CLOSED | red | solid |
| MOVING_OPEN / MOVING_CLOSE | green | 1Hz blink |
| SAFETY_STOP | red | 1Hz blink |
| ERROR | red | 4Hz blink |
| Low VBAT (any state) | yellow | 1Hz blink overlay |

ERROR distinguishable from SAFETY_STOP by blink rate (4Hz vs 1Hz). LED_RED on GPIO6 works even when I2C/PCF dead.

## First Boot

1. Flash MicroPython, upload files (main.py last)
2. Power on → boot.py detects DS3231 default date 2000-01-01 → auto AP
3. Connect to `Coop_Control` → open `192.168.4.1`
4. Click "Synchronizuj czas" → sets DS3231 (UTC→CET)
5. Auto-open/close inactive until RTC synced (y < 2020 skips logic). Manual buttons work always.

## Binary Log Format

`/logs.bin`: 4B header (`total_written` uint32 big-endian) + circular buffer of 365 records × 8B.
Record (`>HHHBB`): `days_since_2025`, `open_min`, `close_min`, `manual_count`, `error_count`.
Sentinel `0xFFFF` = no event. Written once daily at midnight.

## WebREPL

```python
# boot.py starts it automatically
# Connect: https://micropython.org/webrepl/ → host 192.168.4.1:8266, pw: coop
```
