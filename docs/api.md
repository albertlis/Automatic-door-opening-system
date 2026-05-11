# REST API Reference — Smart Coop V2

## Overview

The web server runs on **microdot** (async, MicroPython-compatible).  
It is only active during a WiFi AP session — triggered by double-clicking either physical button.

| Environment | Base URL |
|-------------|----------|
| On-device (ESP32) | `http://192.168.4.1` |
| Local dev (CPython) | `http://localhost:5000` |

**Authentication:** None. The AP network (`Coop_Control`) has WPA2 password `coop123`.  
Physical proximity is the access control — no token or session management.

**Content-Type:** All API endpoints produce and consume `application/json` unless noted.

---

## Endpoints Summary

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/status` | Current gate state, sensor readings, schedule |
| `GET` | `/api/events` | Server-Sent Events stream (live status) |
| `GET` | `/api/config` | Read current configuration |
| `POST` | `/api/config` | Write and validate new configuration |
| `POST` | `/api/time` | Synchronise RTC from browser timestamp |
| `POST` | `/api/manual` | Manually open or close the gate |
| `GET` | `/api/forecast` | 30-day (or N-day) trigger schedule |
| `GET` | `/api/logs` | Event log from persistent binary store |
| `POST` | `/api/reboot` | Soft-reset the device |

---

## GET `/api/status`

Returns a snapshot of the current system state.

**Response — 200 OK**

```json
{
  "state": "IDLE_CLOSED",
  "lux": [4.2, 3.8, 3.5, 4.0, 3.9],
  "time": "07:45",
  "limit_top": false,
  "limit_bottom": true,
  "vbat_v": 3.02,
  "config_warning": "",
  "today_schedule": {
    "wo": 330,
    "wc": 1110,
    "ao": 450,
    "ac": 1320
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `state` | string | Current `GateState` enum value |
| `lux` | float[5] | Last 5 BH1750 readings (lux) |
| `time` | string | RTC local time `HH:MM` (CET or CEST) |
| `limit_top` | bool | `true` = door fully open (LIMIT_TOP active) |
| `limit_bottom` | bool | `true` = door fully closed (LIMIT_BOTTOM active) |
| `vbat_v` | float \| null | DS3231 backup battery voltage (V). `null` before first read |
| `config_warning` | string | Non-empty if runtime config invariant was clamped |
| `today_schedule.wo` | int | Window open (minutes from midnight, local) |
| `today_schedule.wc` | int | Window close (minutes from midnight, local) |
| `today_schedule.ao` | int | Backstop open (minutes from midnight, local) |
| `today_schedule.ac` | int | Backstop close (minutes from midnight, local) |

---

## GET `/api/events`

Server-Sent Events stream. The client receives a `status` JSON payload every 2 seconds without polling.

**Response — 200 OK**  
Content-Type: `text/event-stream`

```
data: {"state":"IDLE_CLOSED","lux":[4.2,3.8,3.5,4.0,3.9],"time":"07:45",...}

data: {"state":"IDLE_CLOSED","lux":[4.1,3.9,3.6,4.1,4.0],"time":"07:45",...}
```

The stream runs indefinitely. Each event is identical in structure to `GET /api/status`.

> **Note:** The ESP32 AP supports a maximum of **2 simultaneous SSE clients**. Additional clients should fall back to polling `/api/status`.

---

## GET `/api/config`

Returns the current configuration as a JSON object.

**Response — 200 OK**

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

Unused fields (e.g. `fixed_hour` when `mode=dynamic`) are preserved in the response.

---

## POST `/api/config`

Validates and saves a new configuration. The invariant is checked against today's computed sunrise/sunset before saving.

**Request body:** same structure as `GET /api/config` response.

**Response — 200 OK**

```json
{ "ok": true }
```

**Response — 400 Bad Request** (invariant violation)

```json
{ "error": "abs_open (06:00) < window_open (08:00)" }
```

Possible error messages:

| Condition | Message pattern |
|-----------|----------------|
| abs_open before window_open | `abs_open (HH:MM) < window_open (HH:MM)` |
| abs_close before window_close | `abs_close (HH:MM) < window_close (HH:MM)` |
| abs_open ≥ abs_close | `abs_open (HH:MM) >= abs_close (HH:MM)` |

On success, the new config is:
1. Written to `/config.json` (persists across reboots)
2. Applied to the running controller immediately
3. Trigger times recalculated for today

---

## POST `/api/time`

Synchronises the DS3231 RTC from a browser-provided UTC timestamp.

**Request body**

```json
{ "timestamp_ms": 1717228800000 }
```

`timestamp_ms` is the value of `Date.now()` in JavaScript — UTC milliseconds since Unix epoch.

**Conversion:** UTC + 1 hour = CET. DST is never stored in the RTC.

**Response — 200 OK**

```json
{ "ok": true }
```

**Example:** `timestamp_ms = 1717228800000` (2024-06-01 08:00:00 UTC)  
→ DS3231 stores **2024-06-01 09:00:00 CET**

> Automatic open/close logic is disabled until the RTC is synchronised (year < 2020 is treated as unsynced). Manual buttons always work.

---

## POST `/api/manual`

Manually commands the gate to open or close. Equivalent to pressing a physical button once.

**Query parameter:** `action=open` or `action=close`

```
POST /api/manual?action=open
POST /api/manual?action=close
```

No request body needed.

**Response — 200 OK**

```json
{ "ok": true }
```

**Response — 400 Bad Request**

```json
{ "error": "invalid action" }
```

Behaviour matches `manual_move()`:
- No-op if gate is already in the requested position or already moving that direction
- Interrupts opposite movement if currently `MOVING_OPEN/CLOSE`
- Works from `SAFETY_STOP` (uses `RECOVERY` trigger → lands in `IDLE_*`, not `MANUAL_HOLD_*`)

---

## GET `/api/forecast`

Returns pre-computed trigger schedule for upcoming days. Data is calculated on the fly — no storage.

**Query parameter:** `days=N` (optional, default 30, max 90)

**Response — 200 OK**

```json
[
  [330, 1110, 450, 1320],
  [329, 1111, 449, 1321],
  ...
]
```

Each entry: `[window_open, window_close, abs_open, abs_close]` — minutes from midnight, local time.  
Values are post-clamp (same logic as `_resolve_times()`).

Use this endpoint to render the 30-day SVG forecast chart on the dashboard, or to verify seasonal config behaviour before deploying changes.

---

## GET `/api/logs`

Returns the full event log from the binary circular buffer on LittleFS.

**Response — 200 OK**

```json
[
  {
    "days_since_2025": 120,
    "open_min": 378,
    "close_min": 1095,
    "manual_count": 0,
    "error_count": 0
  },
  {
    "days_since_2025": 121,
    "open_min": null,
    "close_min": null,
    "manual_count": 0,
    "error_count": 1
  }
]
```

| Field | Type | Description |
|-------|------|-------------|
| `days_since_2025` | int | Days elapsed since 2025-01-01 |
| `open_min` | int \| null | First open event of the day (minutes from midnight). `null` = no open |
| `close_min` | int \| null | Last close event of the day (minutes from midnight). `null` = no close |
| `manual_count` | int | Manual interventions that day |
| `error_count` | int | Safety stop incidents that day |

Records are returned oldest-first. Maximum 365 records (circular buffer wraps annually).  
Returns `[]` if the log file does not exist.

---

## POST `/api/reboot`

Performs a soft reset of the ESP32-C3.

No request body.

**Response — 200 OK** (returned before reset completes)

```json
{ "ok": true }
```

On CPython (local dev), this endpoint is a no-op and returns `200 OK`.

> After reboot, the AP session ends. To reconnect, double-click a physical button again.

---

## Static Pages

The web server also serves the HTML UI:

| URL | Page |
|-----|------|
| `/` | Status dashboard with live SSE updates and 30-day forecast SVG |
| `/config` | Configuration editor with mode toggles and time sync |
| `/logs` | Event log table |
| `/debug` | Raw status values, WebREPL link, reboot button |

---

## Error Handling

All 4xx errors return JSON:

```json
{ "error": "human-readable message" }
```

I2C failures during a request do not cause HTTP errors — the controller transitions to `ERROR` state internally and subsequent `/api/status` calls will reflect `"state": "ERROR"`.
