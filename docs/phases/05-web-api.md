# Phase 5 — Web API (REST) + dev runner + API tests

## Goal

REST API in `src/web.py` on top of `CoopController`, served by microdot.
Single HTML page at `/` (built in Phase 7). Dev runner `run_local.py` on
CPython with mock hardware. API tests written first (TDD).

Single-page design. Browser polls `/api/status` every 3 s (Phase 7).
Forecast JSON is drawn client-side. Fixed windows: forecast 14 days, log newest 50
(no query params, R26).

## Prerequisites

- Phase 2: `src/config.py` base (`DEFAULTS`, `validate_config`, exact keys, ranges).
- Phase 3: `src/state.py` (`status_json()`, `get_forecast(days)`, `desired(now)`),
  `src/config.py` (`default_config()`, `save(cfg, path)`, `ConfigError`).
- Phase 4 (owns all of these, R40):
  - `src/main.py` owns the single move owner and `ap_session` (AP lifecycle, idle
    watchdog R35). 05 defines neither.
  - `wifi.ap_password` config key: default `""` = open AP, `validate_config` accepts
    `""` or str 8..63 chars (R6).
  - `manual_move(action, source)`: `source` REQUIRED (`"button"` | `"web"`), returns bool (R1).
  - `main.py` gives `touch_cb`.
- Phase 6: `logs.log_json(n)` (newest first, file tail read only, R24), `logs.flush()`
  (R29). Until then `/api/status` `events` and `/api/log` are stubs (`[]`).

## Deliverables

| File | Status |
|------|--------|
| `src/web.py` | create |
| `run_local.py` | create (repo root, dev only) |
| `tests/test_api.py` | create, failing first |
| `src/www/index.html` | Phase 7. Until it exists `GET /` returns 503 |
| `.gitignore` | add `config.local.json` if missing |

## TDD protocol

1. Write `tests/test_api.py` (list in §4).
2. Run `uv run pytest tests/test_api.py -v`. All tests must FAIL
   (`ImportError` on `web` counts).
3. Implement `src/web.py` until green.

## 1. Module layout

- Flat imports, MicroPython-safe (no `dataclass`, no `enum`, no `contextlib`).
  Same rule as `state.py`.
- `web.py` imports **nothing** from `main.py`. No circular import.
  `main.py` injects `touch_cb` (AP idle timer) and the controller.
- `CONFIG_PATH` and `INDEX_PATH` are parameters of `create_app`, so tests and
  `run_local.py` never write to `/config.json`.

```python
# src/web.py  (flat imports)
import asyncio
import json
import sys

from microdot import Microdot, Request, Response, send_file   # VERIFY: Response signature, send_file missing-file behaviour

from compat import sleep_ms
from config import ConfigError, save, validate_config

Request.max_content_length = 2048      # attribute confirmed (Context7 /miguelgrinberg/microdot); 413 response VERIFY
MP = sys.implementation.name == "micropython"
CONFIG_PATH = "/config.json" if MP else "config.local.json"
INDEX_PATH = "/www/index.html" if MP else "src/www/index.html"
FORECAST_DAYS = 14                     # fixed, no query param (R26)
LOG_LIMIT = 50                         # fixed newest 50, no query param (R26)
TIME_YEAR_MIN = 2024                   # civil year of CET time, checked after +1 h (R33)
TIME_YEAR_MAX = 2099


def civil_from_days(z):                # z = days since 1970-01-01; pure int, Hinnant algorithm (R33)
    z += 719468
    era = z // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + 3 if mp < 10 else mp - 9
    y = yoe + era * 400
    return (y + 1 if m <= 2 else y), m, d


def create_app(ctrl, touch_cb, config_path: str = CONFIG_PATH,
               index_path: str = INDEX_PATH) -> Microdot:
    app = Microdot()

    @app.before_request                # confirmed (Context7): async def h(request); returning None continues
    async def _touch(request):
        touch_cb()

    @app.errorhandler(404)             # confirmed pattern: return dict, status
    async def _not_found(request):
        return {"error": "not found"}, 404

    # ... routes (§2) ...
    return app
```

## 2. Endpoints

All bodies JSON. All errors JSON `{"error": "<text>"}`.

| Method | Path | Request | 200 response | Error codes |
|--------|------|---------|--------------|-------------|
| GET | `/` | — | `index.html` (`send_file`) | 503 UI file missing |
| GET | `/api/status` | — | `ctrl.status_json()` fields + `events` | — |
| GET | `/api/config` | — | full config dict, `wifi.ap_password` replaced by `wifi.ap_password_set` (R6) | — |
| POST | `/api/config` | full config dict (see 2.2) | `{"ok": true}` | 400 validation, 500 save failed |
| POST | `/api/time` | `{"timestamp_ms": int}` | `{"ok": true}` | 400 type / CET year range, 500 rtc write failed |
| POST | `/api/manual?action=open\|close` | — | `{"ok": true}` | 400 action, 409 SAFETY_STOP / ERROR |
| GET | `/api/forecast` | — | `[[open, close], ...]` always 14 rows | — |
| GET | `/api/log` | — | list, newest first, max 50 (`[]` until Phase 6) | — |
| POST | `/api/reboot` | — | `{"ok": true}`, reset after ~200 ms | — |
| any | unknown path | — | — | 404 via errorhandler |

> Removed from old design: live-stream route, server-side SVG route, extra HTML
> pages, light-sensor fields, CDN UI assets. Every such path returns 404.

### 2.1 `GET /api/status`

```python
@app.get("/api/status")
async def api_status(request):
    s = json.loads(ctrl.status_json())   # status_json() returns a JSON string
    s["events"] = []                     # Phase 6: logs.log_json(5), newest first, file tail read only (R24)
    return s
```

Fields come from `status_json()`: `state`, `time`, `limit_top`, `limit_bottom`,
`config_warning`, `today_schedule` (`{"open","close"}` minutes or `null`),
`rtc_synced`. Plus `events` (last 5, newest first; `[]` in Phase 5). Phase 6 reads
the file tail only (seek `size - 4*n`, R24), never the whole file.

### 2.2 `GET` / `POST /api/config`

- GET returns a copy of `ctrl.config` with `wifi.ap_password` removed and
  `wifi.ap_password_set = bool(stored password)`. The password is never returned (R6, write-only).
  Always complete (defaults merged).
- POST order is fixed. Nothing is changed until the save succeeded:
  1. Parse body. Parse error → 400 `{"error": "invalid JSON"}`.
  2. If `body["wifi"]` is a dict without `ap_password` → copy stored
     `ctrl.config["wifi"]["ap_password"]` into it (keep current password).
     `wifi.ap_password_set` in body is GET-only → rejected by step 3 (unknown key).
  3. `validate_config(d)`. `ConfigError` → 400 `{"error": str(e)}`.
     Validation is strict: missing key, unknown key, bool-as-int, out-of-range,
     bad `wifi.ap_password` (not `""` and not str 8..63) all raise. Non-object raises too (`_check_keys`).
  4. `save(d, config_path)`. `OSError` → 500 `{"error": "save failed"}`,
     `ctrl.config` untouched.
  5. `ctrl.config = d`; `ctrl._date = None` (forces schedule recompute on next
     tick, `config_warning` refreshed).
- Ponytail: step 5 writes a private attr from `web.py`. One caller only.
  Upgrade to `ctrl.invalidate_schedule()` when a second caller appears.
- UI sends back the GET document minus `wifi.ap_password_set`; `wifi.ap_password`
  is included only when the user typed a new one or ticked "clear password" (Phase 7 §4, R39).

### 2.3 `POST /api/time`

- Body must be dict. `timestamp_ms` must be `type(x) is int` (bool rejected).
  Missing / wrong type → 400 `{"error": "timestamp_ms: int required"}`.
- Convert UTC → CET first: `cet_s = ts_ms // 1000 + 3600`. DST never stored; DST is
  applied in logic only (`config.schedule`).
- Civil date and time from `cet_s` with pure-int arithmetic: `days, sod = divmod(cet_s, 86400)`,
  `(y, m, d) = civil_from_days(days)`, `h, mi, s` from `sod`. No stdlib time/epoch conversion (R33).
- Range check AFTER conversion: `TIME_YEAR_MIN <= y <= TIME_YEAR_MAX` (CET year).
  Out → 400 `{"error": "timestamp_ms: year outside 2024..2099"}`.
- Weekday for the 7-tuple: `wd = (days + 3) % 7` (Mon=0; 1970-01-01 was Thursday). Matches Phase 8 driver.
- `ctrl.rtc.set_datetime((y, m, d, h, mi, s, wd))` — 7-tuple, same order as `datetime()` (R22).
  `set_datetime` clears OSF and forces 24 h mode (R17, Phase 8 driver).
  - `OSError` → 500 `{"error": "rtc write failed"}`. `ctrl._date` NOT reset (schedule unchanged).
- Success → `ctrl._date = None`, 200 `{"ok": true}`.

Example: `1717228800000` (2024-06-01 08:00 UTC) → CET 1717232400 → RTC `2024-06-01 09:00`.
Boundary: `1704063600000` (2023-12-31 23:00 UTC) → CET 2024-01-01 00:00 → accepted.
`4102441200000` (2099-12-31 23:00 UTC) → CET 2100-01-01 00:00 → 400.

### 2.4 `POST /api/manual?action=open|close`

```python
@app.post("/api/manual")
async def api_manual(request):
    action = request.args.get("action", "")
    if action not in ("open", "close"):
        return {"error": "invalid action"}, 400
    if ctrl.state == "SAFETY_STOP":
        return {"error": "SAFETY_STOP: clear with physical button at the coop"}, 409
    if ctrl.state == "ERROR":
        return {"error": "ERROR: reboot required"}, 409
    ctrl.manual_move(action, source="web")   # returns bool; web ignores it (R1)
    return {"ok": True}
```

- Web guard gives the 409 text for the UI. `state.manual_move` must ALSO
  refuse `source="web"` while in `SAFETY_STOP` (defence in depth; no
  silent recovery from web). Test covers both.
- Same-direction request while already moving/holding → no-op, still 200 (R1).
- `source="button"` path is Phase 4 (`BTN_OPEN` / `BTN_CLOSE`).

### 2.5 `GET /api/forecast`

- No query params. Always `FORECAST_DAYS = 14` (R26). Query string ignored. No 400 path.
- Body: `[[o, c] for o, c in ctrl.get_forecast(FORECAST_DAYS)]`, minutes local.
- RTC unsynced → `get_forecast` returns `[]` → body `[]`.

### 2.6 `GET /api/log`, `POST /api/reboot`, `GET /`

- `/api/log`: fixed newest 50, no query params, no 400 (R26). Phase 6 reads the
  file tail only (R24), never the whole file. Stub until Phase 6.

```python
@app.get("/api/log")
async def api_log(request):
    return []                            # Phase 6: logs.log_json(LOG_LIMIT), newest first, tail read only
```

- `/api/reboot`: allowed in ANY state (incl. ERROR, SAFETY_STOP).
  Responds first, resets later. Phase 6 flushes the log ring before reset (R29).

```python
@app.post("/api/reboot")
async def api_reboot(request):
    async def _reset_soon():
        await sleep_ms(200)
        if MP:
            import machine
            # Phase 6: logs.flush() here, before reset (R29). Phase 5: nothing to flush.
            machine.reset()
    asyncio.create_task(_reset_soon())   # VERIFY: create_task on MicroPython asyncio
    return {"ok": True}
```

- `GET /`: `send_file(index_path)` if the file exists (`os.stat`), else
  `({"error": "UI not uploaded"}, 503)`. VERIFY `send_file` missing-file
  behaviour; check with `os.stat` first.

### 2.7 Body size and malformed JSON

- `Request.max_content_length = 2048` (decision 5: ~2 KB). Oversized body →
  413 (VERIFY).
- Malformed JSON, wrong top-level type, wrong field type → 400, never 500.
  Wrap `request.json` in `try/except Exception` → 400 (VERIFY which exception
  microdot raises on bad JSON).

## 3. Dependencies on other phases

| Change | Phase | Why |
|--------|-------|-----|
| `manual_move(action, source)` — `source` REQUIRED, returns bool | 4 | web vs button policy (decision 1, R1) |
| `wifi.ap_password` config key + validation | 4 | R6, R40 |
| web refused in `SAFETY_STOP` inside `manual_move` | 4 | defence in depth |
| `ap_session` (AP lifecycle, idle watchdog) | 4 | R35, R40 |
| `logs.log_json(n)` file-tail read; `logs.flush()` before reset | 6 | R24, R29 |
| `status_json` unchanged | — | already has needed keys |
| `get_forecast` returns `[]` when unsynced | — | already so |

## 4. `tests/test_api.py` — failing-first list

Tests are `async def` functions run by pytest-asyncio with `asyncio_mode = "auto"`
(already in `pyproject.toml`, R40). No `asyncio.run` in tests. Use `ctrl` fixture
from `tests/conftest.py` (fixed 06:00/18:00 schedule, RTC 2026-06-15 12:00 CET,
state `IDLE_CLOSED`).
Client: `from microdot.test_client import TestClient`; `client = TestClient(app)`
(confirmed by Context7 / Microdot docs).

| # | Test | Checks |
|---|------|---------|
| 1 | `test_status_shape` | keys `state,time,limit_top,limit_bottom,config_warning,today_schedule,rtc_synced,events`; `state == "IDLE_CLOSED"` |
| 2 | `test_status_no_removed_fields` | no light-sensor or battery field |
| 3 | `test_config_get_shape` | keys `location,schedule,safety,wifi`; `wifi` has `ap_password_set` (bool) and NO `ap_password` key; stored password value absent from body (R6) |
| 4 | `test_config_post_roundtrip` | body = GET doc minus `wifi.ap_password_set`, with `open_after_sunrise_min=45` → 200; GET returns 45; `ctrl.config` updated; file written to `tmp_path`; `ctrl._date is None` |
| 5 | `test_config_post_keeps_password_when_absent` | no `wifi.ap_password` in body → 200; stored password unchanged; `ap_password_set` stays true |
| 6 | `test_config_post_400` (parametrized) | `[]` → 400 not object; raw `{` → `invalid JSON`; `foo` key → `unknown key foo`; `move_timeout_s=500` → contains `safety.move_timeout_s`; `move_timeout_s=True` → 400; `wifi.ap_password_set` in body → `unknown key ap_password_set` |
| 7 | `test_config_post_save_failure_500` | `save` raises `OSError` → 500 `save failed`, `ctrl.config` unchanged |
| 8 | `test_ap_password_validation` (parametrized) | `""` → 200; 7 chars → 400 `wifi.ap_password: must be "" or 8..63 chars`; 8 chars → 200; 64 chars → 400 |
| 9 | `test_time_utc_to_cet` (parametrized) | `1717228800000` → RTC 2024-06-01 09:00; `1704067200000` → 2024-01-01 01:00; `ctrl._date is None` after success |
| 10 | `test_time_400` (parametrized) | `"abc"`, `1.5`, `True` → `timestamp_ms: int required`; `0` → `timestamp_ms: year outside 2024..2099` |
| 11 | `test_time_cet_year_boundary` (parametrized) | `1704063600000` (CET 2024-01-01 00:00) → 200; `1704063599000` (CET 2023-12-31 23:59:59) → 400; `4102441199000` (CET 2099-12-31 23:59:59) → 200; `4102441200000` (CET 2100-01-01 00:00) → 400 (R33, range after CET) |
| 12 | `test_time_rtc_write_failed_500` | `set_datetime` raises `OSError` → 500 `rtc write failed`; `ctrl._date` unchanged |
| 13 | `test_manual_open_from_idle_closed` | 200, `state == "MOVING_OPEN"` |
| 14 | `test_manual_refused` (parametrized) | `action=spin` → 400 `invalid action`; SAFETY_STOP → 409 exact text `SAFETY_STOP: clear with physical button at the coop`; ERROR → 409 `ERROR: reboot required`; state unchanged in both 409 cases |
| 15 | `test_manual_web_refused_in_safety_stop` | `ctrl.manual_move("open", source="web")` in SAFETY_STOP → returns False, no state change (state level, defence in depth) |
| 16 | `test_forecast_14` | len 14, each `[int, int]`; extra `days` query param ignored, still 14 |
| 17 | `test_forecast_unsynced_empty` | RTC year 1999 (or OSF=1) → `[]` |
| 18 | `test_index_200_and_503` | `index_path` = tmp html → 200, `text/html`; `index_path` missing → 503 `UI not uploaded` |
| 19 | `test_reboot_200_any_state` (parametrized) | ERROR and SAFETY_STOP → 200 |
| 20 | `test_touch_cb_called` | touch_cb called once per request |
| 21 | `test_removed_routes_404` | every route in the "Removed from old design" note → 404 |
| 22 | `test_unknown_route_json_404` | `/nope` → 404, JSON `{"error": "not found"}` |
| 23 | `test_log_fixed_list` | `/api/log` → 200, list; extra `limit` query (`5`, `abc`) → 200 list, ignored, no 400 (R26) |

Count: 23 functions (parametrized cases expand at collection). Owner target ~26;
23 after merging. Phase 7 adds 1 (`test_index_under_size_budget`, §5 of 07) → 24.
Run before implementing: all FAIL.

## 5. `run_local.py` (dev only, CPython)

Plan. Repo root. Not uploaded to device.

```python
"""Dev runner: mock hardware + web UI on http://localhost:5000."""
import asyncio
import sys

sys.path.insert(0, "src")                     # flat imports in src/
from tests.mock_hardware import make_ctrl     # make_ctrl(config=None, **pins)
from web import create_app


async def _tick_loop(ctrl):
    while True:
        ctrl.tick()
        await asyncio.sleep(2)


async def main():
    ctrl = make_ctrl()                        # RTC 2026-06-15 12:00 CET, synced
    app = create_app(ctrl, touch_cb=lambda: None, config_path="config.local.json")
    tick = asyncio.create_task(_tick_loop(ctrl))
    print("Running at http://localhost:5000")
    server = asyncio.create_task(app.start_server(port=5000))   # Context7: create_task(app.start_server()) pattern; port kwarg VERIFY
    await server
    tick.cancel()


if __name__ == "__main__":
    asyncio.run(main())
```

- `make_ctrl` signature confirmed: `make_ctrl(config: dict | None = None, **pins)`.
- Server start: `await app.start_server(...)` runs on the existing loop (Context7
  confirmed coroutine form). `app.run(port=...)` is NOT used here: it starts its own
  loop, so it cannot share `asyncio.run(main())` with `_tick_loop` (R10).
- `config.local.json` is written by POST `/api/config` in dev. Must be in `.gitignore`.
- `touch_cb` is a no-op here (no AP timer on CPython).

## Acceptance

```bash
uv run pytest tests/test_api.py -v          # 23 functions green (+1 size test from Phase 7)
uv run ruff check src/web.py run_local.py tests/test_api.py
uv run python run_local.py                  # then:
curl -s http://localhost:5000/api/status    # JSON with state IDLE_CLOSED
curl -s http://localhost:5000/api/forecast  # 14 rows
curl -s http://localhost:5000/api/log       # [] until Phase 6
```

## VERIFY ON DEVICE / library

- `Request.max_content_length` → actual 413 status and body on MicroPython
  (attribute confirmed by Context7; `max_body_length` also exists there).
- `request.json` exception type on malformed body (catch-all used now).
- `send_file` on MicroPython LittleFS (chunking, 15 KB file, memory).
- `TestClient` `.json`, `.status_code`, POST body keyword (`json=` vs `body=`).
- `start_server(port=...)` keyword on CPython.
- `asyncio.create_task` + `sleep_ms` inside reboot handler on MicroPython.
- (closed) `wd` Mon=0 matches Phase 8 DS3231 driver (reg 1..7 minus 1).
- `set_datetime` clears OSF on real DS3231 (R17).
- `logs.flush()` before `machine.reset()` persists the ring on device (R29, Phase 6/8).

## Notes

- Test-client usage (confirmed by Context7 / Microdot test-client docs):
  `client = TestClient(app)`, `response = await client.get('/')`,
  `response.text`. The older `app.test_client()` form is NOT used.
- Microdot returns `dict`/`list` as JSON automatically; `(body, status)` and
  `(body, status, headers)` tuples supported (confirmed).
- Errors: always `(dict, status)` tuples. Keeps one format across routes.
