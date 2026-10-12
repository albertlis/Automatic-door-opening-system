# REST API Reference — Smart Coop V2

## Overview

Web server: microdot (async, MicroPython-compatible). Active only during a WiFi AP
session. The AP starts when both physical buttons are held for 3 s (Phase 4, R7),
or automatically once per boot when the RTC is unsynced or OSF=1 (R8).
Auto-off after 10 min without HTTP activity.

| Environment | Base URL |
|-------------|----------|
| On device (ESP32-C3) | `http://192.168.4.1` |
| Local dev (CPython) | `http://localhost:5000` (`run_local.py`) |

**Access:** AP `Szczesliwy_Kurczak`. WPA2 when `wifi.ap_password` is non-empty (8..63 chars).
Empty password = open AP (R6). The password is write-only: `GET /api/config`
returns only `wifi.ap_password_set` (bool). No token, no session. Physical
proximity is the access control.

**WebREPL:** `wifi.webrepl_password` (`""` = off, else 4..9 printable ASCII). Runs only while the AP session is up (`webrepl.start(password=...)` after `ap_on`, `webrepl.stop()` before `ap_off`). Write-only like the AP password: `GET /api/config` returns only `wifi.webrepl_password_set` (bool).

**Content type:** requests with a body send `application/json`. Responses are
`application/json`, except `GET /` (`text/html`).

**Time values:** minutes from local midnight (CET or CEST). The RTC stores CET
always; DST is applied in logic only.

**Errors:** every 4xx / 5xx body is `{"error": "<text>"}`.

---

## Endpoints summary

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Single-page UI (`index.html`) |
| GET | `/api/status` | State, time, limits, today's schedule, last 5 events |
| GET | `/api/config` | Read configuration (AP and WebREPL passwords never returned) |
| POST | `/api/config` | Validate, save and apply configuration |
| POST | `/api/time` | Set RTC from browser UTC timestamp |
| POST | `/api/manual?action=open\|close` | Manual move (refused in SAFETY_STOP / ERROR) |
| GET | `/api/forecast` | Schedule for the next 14 days (fixed, no query params) |
| GET | `/api/log` | Newest 50 events, newest first (RAM + file tail, Phase 6). No query params |
| POST | `/api/reboot` | Soft reset (allowed in any state) |
| GET | `/api/diag` | Diagnostics: I2C devices, raw pins, RTC OSF, clock, memory, uptime, AP clients. 404 when not wired |

---

## GET `/api/status`

**200 OK** (example values)

```json
{
  "state": "IDLE_CLOSED",
  "time": "07:45",
  "limit_top": false,
  "limit_bottom": true,
  "config_warning": "",
  "today_schedule": { "open": 412, "close": 1094 },
  "rtc_synced": true,
  "events": []
}
```

| Field | Type | Description |
|-------|------|-------------|
| `state` | string | `INIT`, `IDLE_OPEN`, `IDLE_CLOSED`, `MOVING_OPEN`, `MOVING_CLOSE`, `MANUAL_HOLD_OPEN`, `MANUAL_HOLD_CLOSED`, `SAFETY_STOP`, `ERROR` |
| `time` | string \| null | RTC local time `HH:MM`. `null` if RTC read failed |
| `limit_top` | bool | `true` = door fully open (LIMIT_TOP active) |
| `limit_bottom` | bool | `true` = door fully closed (LIMIT_BOTTOM active) |
| `config_warning` | string | Non-empty if today's open ≥ close (no auto moves today) |
| `today_schedule` | object \| null | `{open, close}` minutes. `null` when RTC unsynced |
| `rtc_synced` | bool | `false` while year < 2020 or OSF flag set |
| `events` | array | Last 5 events, newest first (`logs.log_json(5)`, Phase 6 format), read from file tail only (R24). `[]` until Phase 6 |

`today_schedule.open` = sunrise (−0.833°) + `schedule.open_after_sunrise_min`.
`today_schedule.close` = civil dusk (−6°) + `schedule.close_after_dusk_min`.

---

## GET `/api/config`

**200 OK**

```json
{
  "location": { "lat": 50.01, "lon": 20.99 },
  "schedule": { "open_after_sunrise_min": 30, "close_after_dusk_min": 0, "open_not_before_min": null, "close_not_after_min": null },
  "safety": { "move_timeout_s": 21 },
  "wifi": { "ap_password_set": false, "webrepl_password_set": false }
}
```

Always the full document (defaults merged in).

| Key | Range | Meaning |
|-----|-------|---------|
| `location.lat` | −90 .. 90 (number) | Latitude, degrees |
| `location.lon` | −180 .. 180 (number) | Longitude, degrees |
| `schedule.open_after_sunrise_min` | −120 .. 180 (integer) | Offset after sunrise |
| `schedule.close_after_dusk_min` | −120 .. 180 (integer) | Offset after civil dusk |
| `schedule.open_not_before_min` | `null` or 0 .. 1439 (integer) | Local-time floor for open (minutes since midnight). `null` = off. Applied after offsets and DST |
| `schedule.close_not_after_min` | `null` or 0 .. 1439 (integer) | Local-time ceiling for close (minutes since midnight). `null` = off. Applied after offsets and DST |
| `safety.move_timeout_s` | 5 .. 120 (integer) | Motor timeout → SAFETY_STOP |
| `wifi.ap_password_set` | bool (GET only) | `true` when a non-empty AP password is stored. The password itself is never returned |
| `wifi.webrepl_password_set` | bool (GET only) | `true` when a non-empty WebREPL password is stored. The password itself is never returned |

---

## POST `/api/config`

Body: the document as in GET, with two differences:
- `wifi.ap_password` is **optional**. Absent = keep the stored password.
  Present = set it: `""` = open AP, otherwise str 8..63 chars.
- `wifi.webrepl_password` is **optional** too. Absent = keep the stored password.
  Present = set it (`""` = WebREPL off, else 4..9 printable ASCII).
- `wifi.ap_password_set` and `wifi.webrepl_password_set` are GET-only and are **not accepted** (400).

UI (Phase 7, R39): checkbox "clear password (open AP)" sends `""`. Blank field = keep.

**200 OK**

```json
{ "ok": true }
```

**400 Bad Request** — examples (exact `error` text):

| Cause | `error` |
|-------|---------|
| Body not JSON | `invalid JSON` |
| Unknown key | `config: unknown key foo` |
| Missing key | `schedule: missing key close_after_dusk_min` |
| Out of range | `schedule.open_after_sunrise_min: 200 outside -120..180` |
| Wrong type (incl. bool) | `safety.move_timeout_s: wrong type` |
| AP password length | `wifi.ap_password: must be "" or 8..63 chars` |
| GET-only key sent | `wifi: unknown key ap_password_set` |
| GET-only key sent | `wifi: unknown key webrepl_password_set` |

**500 Internal Server Error** — `{"error": "save failed"}`. Config in RAM unchanged.

On success, in this order: written to `/config.json` (atomic tmp + rename), applied
to the running controller, today's schedule recalculated on the next tick.

---

## POST `/api/time`

Set the DS3231 from a browser UTC timestamp.

**Request**

```json
{ "timestamp_ms": 1717228800000 }
```

`timestamp_ms` = JavaScript `Date.now()`. Must be an integer (not bool, not float).
Converted to CET (UTC + 1 h) first; the year range is checked on the CET date:
2024..2099 (inclusive).

**200 OK**

```json
{ "ok": true }
```

**400 Bad Request**

| Cause | `error` |
|-------|---------|
| Missing / not int | `timestamp_ms: int required` |
| CET year outside 2024..2099 | `timestamp_ms: year outside 2024..2099` |

**500 Internal Server Error**

| Cause | `error` |
|-------|---------|
| RTC write failed (I2C `OSError` on `set_datetime`) | `rtc write failed` |

On 500 the schedule cache is not reset (`config_warning` / today's schedule unchanged).

**Conversion:** UTC + 1 h = CET. Example: `1717228800000` (2024-06-01 08:00 UTC)
→ RTC `2024-06-01 09:00` CET.

Side effects: clears OSF flag; schedule recalculated.

> Automatic moves are disabled until the RTC is synced (year < 2020 or OSF set).
> Manual moves always work (except in SAFETY_STOP / ERROR).

---

## POST `/api/manual`

Query: `action=open` or `action=close`. No body.

```
POST /api/manual?action=open
POST /api/manual?action=close
```

**200 OK**

```json
{ "ok": true }
```

**400 Bad Request**

```json
{ "error": "invalid action" }
```

**409 Conflict**

| State | `error` |
|-------|---------|
| `SAFETY_STOP` | `SAFETY_STOP: clear with physical button at the coop` |
| `ERROR` | `ERROR: reboot required` |

Behaviour (web source):
- The web layer checks state itself for the 409 (R1); the bool returned by the controller is ignored.
- Same direction as current state → no-op, still 200.
- Opposite direction while moving → reverses (after coast dead-time, decision 11).
- From `IDLE_*` / `MANUAL_HOLD_*` → lands in `MANUAL_HOLD_*`.
- **Never** clears SAFETY_STOP. Only the physical button does (409 above).

---

## GET `/api/forecast`

No query params. Always 14 days (fixed, R26). A query string is ignored.

```
GET /api/forecast
```

**200 OK** — 14 entries (first 3 shown)

```json
[
  [412, 1094],
  [411, 1095],
  [410, 1096]
]
```

Each entry: `[open, close]` minutes from local midnight, same rules as `/api/status`.
Empty list `[]` when RTC unsynced.

---

## GET `/api/log`

Recent events from the RAM buffer and file tail (Phase 6). **Newest first.**

Fixed: newest 50 events. No query params, no 400 (R26). The file is read from its
tail only, never the whole file (R24).

```
GET /api/log
```

**200 OK** — JSON array, newest first, max 50. Empty `[]` until Phase 6 is built.
Record format is defined in the Phase 6 doc.

---

## POST `/api/reboot`

Soft reset of the ESP32-C3. No body. Allowed in any state (including ERROR, SAFETY_STOP).
The log ring is flushed to file before the reset (R29).

**200 OK** (sent before reset, reset after ~200 ms)

```json
{ "ok": true }
```

On CPython (`run_local.py`) the reset is a no-op.

> After reboot the AP session ends. Reconnect by holding both physical buttons for 3 s
> (R7), or wait for the automatic AP if the RTC is still unsynced (R8).

---

## GET `/api/diag`

Diagnostics snapshot, computed per request. Never errors on a bare board: failed
I2C probes give `false` / `null`.

**200 OK**

```json
{
  "rtc": true, "pcf": true, "osf": false,
  "top": 1, "bottom": 0, "nfault": 1, "b_open": 1, "b_close": 1,
  "mhz": 80, "mem": 117, "fs_kb": 800, "up_s": 42, "clients": null
}
```

| Field | Type | Description |
|-------|------|-------------|
| `rtc` | bool | DS3231 (0x68) answered `i2c.scan()` |
| `pcf` | bool | PCF8574 (0x20) answered `i2c.scan()` |
| `osf` | bool \| null | DS3231 OSF flag. `null` when `rtc` is `false` |
| `top` / `bottom` | 0 \| 1 | Raw LIMIT_TOP / LIMIT_BOTTOM pin. `0` = hit |
| `nfault` | 0 \| 1 | Raw DRV8833 nFAULT pin. `0` = fault |
| `b_open` / `b_close` | 0 \| 1 | Raw BTN_OPEN / BTN_CLOSE pin. `0` = pressed |
| `mhz` | int | CPU frequency, MHz |
| `mem` | int | `gc.mem_free() // 1024`, KB |
| `fs_kb` | int | Free flash in `/`, KB (`f_frsize * f_bavail`) |
| `up_s` | int | Seconds from `ticks_ms()` (compat shim) |
| `clients` | int \| null | AP stations connected. `null` when the AP is off |

Only on device, with `main.py` wiring. `run_local.py` serves a fixed fake payload.

## GET `/`

`text/html`, the single-page UI. If the file is not uploaded yet: 503 JSON.

---

## Error handling summary

| Code | When |
|------|------|
| 400 | Malformed JSON, wrong type, out of range, invalid action, invalid time, invalid AP password, GET-only key sent to POST /api/config |
| 404 | Unknown path. Body `{"error": "not found"}` |
| 409 | Manual move refused in SAFETY_STOP / ERROR |
| 413 | Body larger than 2 KB (VERIFY on device) |
| 500 | Config save failed (`save failed`); RTC write failed (`rtc write failed`, `/api/time`) |
| 503 | UI file missing (`GET /`) |

I2C failures during a request do not cause HTTP errors. The controller goes to
`ERROR` internally after 3 consecutive failures; `/api/status` then reports
`"state": "ERROR"`.
