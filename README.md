# 🐔 Smart Coop V2

**Automatic chicken coop door controller — ESP32-C3 · MicroPython · asyncio · local web UI**

[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![MicroPython](https://img.shields.io/badge/MicroPython-1.23%2B-2b5797?logo=micropython&logoColor=white)](https://micropython.org/)
[![Platform](https://img.shields.io/badge/platform-ESP32--C3-red?logo=espressif&logoColor=white)](https://www.espressif.com/en/products/socs/esp32-c3)
[![License: CC BY-NC-SA 4.0](https://img.shields.io/badge/license-CC%20BY--NC--SA%204.0-lightgrey?logo=creativecommons)](LICENSE)

---

Opens and closes a chicken coop door on a schedule tied to sunrise and dusk. Built around an ESP32-C3, a self-braking DC gear motor, and a local-only WiFi access point for configuration. No cloud, no router dependency, WiFi off by default.

---

## Status

| Part | State |
|------|-------|
| Phase 1 — astronomy (sunrise, civil dusk, DST) | Done |
| Phase 2 — config load / save / validate | Done |
| Phase 3 — synchronous state machine (`CoopController`) | Done |
| Phase 4 — asyncio tasks, buttons, move driver | Planned |
| Phase 5 — web API | Planned |
| Phase 6 — event log | Planned |
| Phase 7 — single-page web UI | Planned |
| Phase 8 — ESP32 hardware driver, boot, on-device checks | Planned |

Phases 1–3 run and are covered by the CPython test suite (133 tests passing). Phase specs: [docs/phases/](docs/phases/).

---

## Features

- **Schedule by sun** — opens a set number of minutes after sunrise, closes a set number of minutes after civil dusk. Location is set in config (lat/lon).
- **Manual control** — two physical buttons. Press and release under 1 s: BTN_OPEN opens, BTN_CLOSE closes. Both held 3 s: starts the WiFi config page. One button held ≥ 1 s: no action.
- **Safety stop** — if the door does not reach its limit switch within the move timeout (21 s by default), the motor stops and the controller latches `SAFETY_STOP`. Only a physical button clears it.
- **Config page on demand** — WiFi AP `Szczesliwy_Kurczak`, one page, works offline (no CDN). Starts on demand (both buttons, 3 s), and once per boot while the RTC is unsynced or OSF = 1. Auto-off after 10 min without HTTP activity. Never starts while the door is moving; deferred until idle.
- **Real-time clock** — DS3231 keeps CET (UTC+1) year-round. Daylight saving is applied in software only. Until the clock is synced, the door does not move automatically.
- **RAM event log** — recent open / close / safety-stop events, flushed to flash once a day.
- **Low power** — idle light sleep between control ticks (planned; power figures to be measured).
- **Testable without hardware** — dependency injection throughout. The full test suite runs on CPython with mock hardware.

---

## Hardware at a Glance

| Component | Part | Purpose |
|-----------|------|---------|
| MCU | ESP32-C3 Super Mini | WiFi, USB, 3.3 V logic |
| Motor driver | DRV8833 | 1.5 A peak, nSLEEP power gating |
| DC motor | JGY-370 (6 V, worm gear) | Self-braking, 0 mA holding current |
| GPIO expander | PCF8574 (I2C 0x20) | Two LEDs + motor IN1/IN2 |
| RTC | DS3231 (ZS-042) | TCXO ±2 ppm, CR2032 backup — board must be modified |
| Limit switches | 2× microswitch | Door-open / door-closed detection |
| Push buttons | 2× momentary | Manual open / close, config page trigger |
| LEDs | red, green, yellow | Door and fault status |

> Full pinout, RC filters, DS3231 modification and power notes: [docs/hardware.md](docs/hardware.md)

---

## Getting Started

### Local Development (CPython)

Requires Python 3.13+ and [`uv`](https://github.com/astral-sh/uv).

```bash
# Clone and install
git clone https://github.com/albertlis/Automatic-door-opening-system.git
cd Automatic-door-opening-system
uv sync --extra dev

# Run the test suite
uv run pytest tests/ -v

# Lint and format
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

Local web server (`python run_local.py`, mock hardware, `http://localhost:5000`) is part of phase 5 and not in the tree yet.

Development workflow: TDD. Tests are written first, then the code is implemented until they pass.

---

### Deploy to ESP32-C3

> Deployment steps apply once phases 4–8 are complete. Current code is tested on CPython only.

#### 1. Flash MicroPython

Download the latest `ESP32_GENERIC_C3` firmware from [micropython.org/download](https://micropython.org/download/ESP32_GENERIC_C3/).

```bash
esptool --chip esp32c3 --port COM<N> erase_flash
esptool --chip esp32c3 --port COM<N> --baud 460800 write_flash -z 0x0 ESP32_GENERIC_C3-*.bin
```

#### 2. Modify the DS3231 Board

> ⚠️ **Do this before installing the CR2032 battery.**
>
> Remove the charging resistor (~200 Ω) on the ZS-042 board. CR2032 is non-rechargeable; the charging path can overheat and cause a fire.
> Removing the power LED and its resistor is optional and cuts standby drain.
> See [docs/hardware.md](docs/hardware.md#ds3231-zs-042--modification-required) for details.

#### 3. Deploy

Prereqs: `uv sync --extra dev` (deploy reads microdot from `.venv`), and `uvx` on PATH (fetches `mpy-cross==1.29.0.post2` and `mpremote`).

```bash
uv run python scripts/deploy.py COM<N>           # update
uv run python scripts/deploy.py COM<N> --first   # first flash only: also uploads config.json
```

`--first` copies `config.default.json` → `:config.json`. Without it, `:config.json` is never touched (live schedule, location, AP password survive). `:logs.bin` is never touched.

WiFi mode (no USB): hold both buttons 3 s → AP up → WebREPL on `192.168.4.1:8266`. The `--wifi` password must match the device's `webrepl.start(password=...)`.

```bash
uv run python scripts/deploy.py --wifi PASSWORD              # host 192.168.4.1
uv run python scripts/deploy.py --wifi PASSWORD --host IP    # other host
```

- Same build as USB; `coop.mpy` sent last among `.mpy`, `main.py` last overall. Upload client: `scripts/webrepl_client.py` (stdlib only, derived from micropython/webrepl, MIT).
- Never touches `:config.json` or `:logs.bin`. `--first` and a COM port are refused with `--wifi`.
- WebREPL cannot rm or mkdir: stale device `.py` copies stay (a warning is printed). Run one USB deploy to clean them.
- Reset: sends `machine.reset()` as REPL text. If the device does not reboot, reset it by hand.
- If wifi upload aborts midway, redeploy over USB (device may not boot).
- Only checked locally: `uv run python scripts/webrepl_client.py --selftest` (fake device, frame encoding). NIEPRZETESTOWANE on a real device.

`scripts/deploy.py`:
- Compiles `src/{astro,compat,config,dns,hardware,logs,state,web}.py` and microdot to `.mpy` with `uvx --from mpy-cross==1.29.0.post2`. `src/main.py` → `coop.mpy`.
- Deletes the device `.py` for those modules and microdot. Creates `:microdot` and `:www` (errors ignored if they exist).
- Uploads all `.mpy` files.
- Gzips `src/www/index.html` (stdlib, level 9, mtime 0) → `:/www/index.html.gz`.
- Uploads `src/boot.py` → `:boot.py` as plain `.py` (MicroPython runs boot.py only as `.py`).
- Writes device `main.py` last: `import coop` / `coop.start()`.

Why `.mpy`: compiling `.py` on the device grows the MicroPython heap into IDF DRAM, and it never shrinks. Measured IDF free before `ap_on`: 3.6 KB with `.py`, 104 KB with `.mpy`. With `.py`, the AP gave "WiFi Out of Memory" when a client connected.

#### 4. First Boot

1. Power on. If the RTC is unsynced or OSF = 1 (never set, or backup cell dead), the WiFi AP starts automatically ~14 s after boot, once per boot (10 min idle timeout).
2. Connect to `Szczesliwy_Kurczak`. Fresh install: `wifi.ap_password` is empty, so the AP is **open**. Set a password (8–63 characters) on the config page first.
3. Open `http://192.168.4.1` and press **sync time**. The browser's clock is written to the RTC. While the AP is up, any hostname except `.local` redirects to the config page (captive DNS).
4. Automatic open / close starts once the RTC is synced.
5. Later boots: WiFi stays off. Hold both buttons for 3 s to open the config page.

---

## Configuration

Stored in `config.json` on the device. Defaults shipped in [`config.default.json`](config.default.json):

```json
{
  "location": { "lat": 50.01, "lon": 20.99 },
  "schedule": { "open_after_sunrise_min": 30, "close_after_dusk_min": 0 },
  "safety":   { "move_timeout_s": 21 },
  "wifi":     { "ap_password": "" }
}
```

| Key | Default | Meaning |
|-----|---------|---------|
| `location.lat` / `location.lon` | 50.01 / 20.99 | Coop position, used for sunrise and civil dusk |
| `schedule.open_after_sunrise_min` | 30 | Door opens this many minutes after sunrise |
| `schedule.close_after_dusk_min` | 0 | Door closes this many minutes after civil dusk (−6°) |
| `safety.move_timeout_s` | 21 | Move time limit before `SAFETY_STOP` |
| `wifi.ap_password` | `""` (empty) | AP password, 8–63 characters when set. Empty = **open AP** (see security note) |

Times are computed with the NOAA sunrise formula (±2 min accuracy). Invalid config is rejected on save; a corrupt file on boot falls back to defaults.

> **Security — AP is OPEN while `wifi.ap_password` is empty.** On a fresh install the `Szczesliwy_Kurczak` AP has no password. Anyone in WiFi range can open the config page and move the door. The config page shows a red "Set AP password" banner while the field is empty. Set a password (8–63 characters) right after installation. The password is write-only: the config page never returns it, only `ap_password_set`.

---

## Web Interface

Available only while the AP is on. One page at `http://192.168.4.1`. Polls status every 3 s. Works with no internet connection.

Shows: door state, device time, RTC sync status, today's open / close times, limit switches, a 14-day forecast, recent events, open / close buttons, time sync, config form, reboot.

REST endpoints and request / response formats: [docs/api.md](docs/api.md).

---

## LED Status Reference

| State | LED | Pattern | Meaning |
|-------|-----|---------|---------|
| Door open (idle or manual hold) | Green | Solid | Fully open |
| Door closed (idle or manual hold) | Red | Solid | Fully closed |
| Moving | Green | 1 Hz blink | Door in motion |
| `SAFETY_STOP` | Red | 1 Hz blink | Motor stopped — press a physical button to recover |
| `ERROR` | Red | **4 Hz blink** | Hardware fault — reboot required |
| RTC not synced | Yellow | 1 Hz blink | Set the time from the config page |

`ERROR` is distinguished from `SAFETY_STOP` by blink speed. The red LED is wired directly to the MCU and still works if the I2C bus is dead.

---

## Documentation

| Document | Description |
|----------|-------------|
| [docs/hardware.md](docs/hardware.md) | BOM, power, pin tables, RC filters, PCF8574 wiring, DS3231 modification, DRV8833 notes |
| [docs/phases/](docs/phases/) | Per-phase specs: goal, deliverables, tests, verification |
| [docs/state-machine.md](docs/state-machine.md) | State machine and decision logic |
| [docs/api.md](docs/api.md) | REST API reference |
| [docs/migration.md](docs/migration.md) | Historical v1 → v2 planning notes (outdated) |
| [CLAUDE.md](CLAUDE.md) | Developer reference for the codebase |
| [legacy/README.md](legacy/README.md) | v1 firmware (ATmega328P / Arduino / C++) |

---

## Project History

**V1** (ATmega328P / Arduino / C++) — code in [`legacy/`](legacy/README.md). Replaced due to:
- DS1307 RTC drift required a nightly correction
- MG995 servo drew 400–600 mA stall current 24/7 to hold the door
- No USB, no remote visibility

**V2** (this codebase) — ESP32-C3, MicroPython, self-braking worm gear motor, DS3231 RTC, WiFi AP config page.

---

## License

This project — including source code, documentation, and wiring diagrams — is licensed under the
**Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International License (CC BY-NC-SA 4.0)**.

Commercial use is strictly prohibited. This includes selling the software, offering paid installation
services, incorporating it into a commercial product, or any internal business use.
Commercial licensing is available — contact the author for written permission.

See the [LICENSE](LICENSE) file for the full license text, or visit
[creativecommons.org/licenses/by-nc-sa/4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/).
