# Phase 7 — HTML UI (single page)

## Goal

One static page `src/www/index.html`: status, manual control, forecast, time sync,
config form, reboot. Mobile-first. Works on the coop AP with NO internet.
Vanilla JS, inline CSS, no external resources of any kind.

## Prerequisites

- Phase 5 complete: `/api/status`, `/api/config` (GET/POST), `/api/time`,
  `/api/manual`, `/api/forecast` (fixed 14 days), `/api/log`, `/api/reboot`, `GET /` serving the file.
- Phase 5 also provides `wifi.ap_password_set` (bool) in `GET /api/config` (R6).
- Phase 6 complete: `events` in `/api/status` = last 5, newest first (may be `[]`).

## Deliverables

| File | Status |
|------|--------|
| `src/www/index.html` | create |
| `tests/test_api.py` | add size-budget test (§5) |

Only one file. No other pages, no build step, no npm.

## Design rules

- Inline `<style>` and `<script>`. Zero external URLs. Phone on the AP has no internet.
- CSS lib: **simple.css v2.3.7** (MIT, npm `simpledotcss`, 9429 B minified), inlined into `<style>` with license comment. Classless; dark mode auto via `prefers-color-scheme`. Own CSS after the lib: badge state colours, banners, `.card`, ERROR blink, touch sizes. Rejected: water.css 2 (22668 B, light only), MVP.css 1.18 (10264 B, dark mode only with `color-mode="user"` attr), Pico classless 2 (71040 B), new.css (npm/jsDelivr fetch failed).
- UI labels in Polish (project UI language). Code, comments and this doc in English.
- Server data rendered with `textContent` / DOM nodes, never `innerHTML` with
  server strings (config warning, events, error text).
- Poll `/api/status` every 3 s. One request in flight at a time (`busy` flag).
- Size budget: **gzip -9 < 10 KB (served as /www/index.html.gz)** (MicroPython LittleFS, RAM for one send).
- Mobile-first: single column, `max-width: 32rem`, buttons min-height 44 px,
  font-size 16 px (no iOS zoom), no hover-only actions.

## 1. Layout (top to bottom)

| Block | Element id | Content |
|-------|-----------|---------|
| Banner | `#banner` | offline / config warning (hidden when empty) |
| AP warning | `#apwarn` | red, `Ustaw hasło AP (sieć jest otwarta)` when `wifi.ap_password_set` false; hidden otherwise. Separate from `#banner` (poll clears `#banner`) |
| State | `#state` | badge: state name, colour by state |
| Time | `#time` | `RTC: HH:MM` or `Czas nieustawiony` when `rtc_synced` false |
| Schedule | `#sched` | today open / close `HH:MM`, or `—` when `today_schedule` null |
| Limits | `#limits` | GÓRA / DÓŁ, ✓ / ✗ |
| Control | `#ctl` | buttons Otwórz / Zamknij (disabled in SAFETY_STOP / ERROR) |
| Stop message | `#stopmsg` | server 409 text shown verbatim, see §3 |
| Forecast | `#fc` | inline SVG, 14 days (drawn by JS) |
| Events | `#ev` | last 5 events, newest first (Phase 6 format) or `—` |
| Time sync | `#sync` | button Synchronizuj czas + result text |
| Config | `#cfg` | form, see §4 |
| Reboot | `#reboot` | button with confirm |

## 2. Badge colours (CSS selectors = state names)

| State | Background | Note |
|-------|-----------|------|
| IDLE_OPEN, MANUAL_HOLD_OPEN | green | |
| IDLE_CLOSED, MANUAL_HOLD_CLOSED | red | |
| MOVING_OPEN, MOVING_CLOSE | amber | |
| SAFETY_STOP | orange | message shown |
| ERROR | dark red | blink 4 Hz (matches LED signal) |
| INIT | grey | |

```css
.badge{display:inline-block;padding:.3em .8em;border-radius:1em;font-weight:700;color:#fff}
.IDLE_OPEN,.MANUAL_HOLD_OPEN{background:#2a7}
.IDLE_CLOSED,.MANUAL_HOLD_CLOSED{background:#c33}
.MOVING_OPEN,.MOVING_CLOSE{background:#e90;color:#000}
.SAFETY_STOP{background:#f60}
.ERROR{background:#900;animation:b .25s step-end infinite}
@keyframes b{50%{opacity:0}}
```

## 3. Behaviour

### 3.1 Polling

```js
let busy = false, fails = 0;
async function poll() {
  if (busy) return;
  busy = true;
  try {
    const r = await fetch("/api/status");
    if (!r.ok) throw new Error(r.status);
    render(await r.json());
    fails = 0;
    banner("");
  } catch (e) {
    if (++fails >= 2) banner("Brak połączenia z kurnikiem");
  } finally {
    busy = false;
  }
}
setInterval(poll, 3000);
poll();
```

- Two consecutive failures → offline banner. Next success clears it.
- Poll keeps running during SAFETY_STOP / ERROR (state must stay visible).

### 3.2 Control buttons

- Enabled when state not in `SAFETY_STOP`, `ERROR`.
- In `SAFETY_STOP`: both disabled, `#stopmsg` = `SAFETY_STOP: clear with physical button at the coop`
  (exact server text; UI shows it verbatim, no rewording).
- In `ERROR`: `#stopmsg` = `ERROR: reboot required`. Reboot button stays enabled.
- POST `/api/manual?action=open|close`. Non-200 → show `error` field from JSON in `#stopmsg`.
- No confirm dialog on move buttons (decision: fewer taps on phone). Reboot keeps `confirm()`.

### 3.3 Time sync

- Button POST `/api/time` with body `{"timestamp_ms": Date.now()}`.
- Result text: `Zsynchronizowano` on 200, else `Błąd: <error>` (incl. `rtc write failed` on 500).
- Shown prominently when `rtc_synced` is false (yellow hint next to time).

### 3.4 Forecast (client-side SVG)

- On load and after successful config save: GET `/api/forecast` (fixed 14 days, no params, R26).
- Response: `[[open, close], ...]` minutes from midnight local. Empty list → text `Brak danych (czas nieustawiony)`.
- Draw one `<svg viewBox="0 0 300 160">`, width 100 %:
  - `x(i) = 30 + i * 270 / (n - 1)` (n = number of rows; n = 1 → x = 30)
  - `y(m) = 4 + m * 140 / 1440` (00:00 top, 24:00 bottom)
  - Two `<polyline>`: open (green `#2a7`), close (orange `#f90`).
  - Y labels 00 / 06 / 12 / 18 / 24, X labels +0 / +7 / +13.
- Built with `document.createElementNS` or with an SVG string built from numbers only
  (no server text inside the SVG). Numbers only → safe to use `innerHTML`.

```js
function drawForecast(rows) {
  const n = rows.length, x = i => 30 + (n > 1 ? i * 270 / (n - 1) : 0),
        y = m => 4 + m * 140 / 1440;
  const pts = k => rows.map((r, i) => x(i).toFixed(1) + "," + y(r[k]).toFixed(1)).join(" ");
  document.getElementById("fc").innerHTML =
    '<svg viewBox="0 0 300 160" style="width:100%;background:#111">' +
    '<polyline fill="none" stroke="#2a7" stroke-width="2" points="' + pts(0) + '"/>' +
    '<polyline fill="none" stroke="#f90" stroke-width="2" points="' + pts(1) + '"/>' +
    '</svg>';
}
```

### 3.5 Events

- `status.events` (last 5, newest first, Phase 6 format). Render as `<li>` with `textContent`.
- Empty → `—`.

### 3.6 Reboot

- `confirm("Na pewno zrestartować urządzenie?")` → POST `/api/reboot`.
- On 200: show `Restart…`, reload page after 5 s. AP session ends on reboot (api.md).

### 3.7 AP password warning

- Source: `wifi.ap_password_set` from `GET /api/config` (on load and after every successful save).
  `/api/status` does not carry it.
- `false` → show `#apwarn` (red): `Ustaw hasło AP (sieć jest otwarta)`. `true` → hidden.
- Text is a fixed HTML string, not server text. Poll never touches `#apwarn`.

## 4. Config form

- On load: GET `/api/config` → keep the full object in memory (`cfgFull`).
  Form shows only the editable fields; untouched keys stay as received.
- Fields:

| Input | JSON path | Type | Range (server) |
|-------|-----------|------|----------------|
| Szerokość | `location.lat` | number | -90..90 |
| Długość | `location.lon` | number | -180..180 |
| Otwarcie po wschodzie (min) | `schedule.open_after_sunrise_min` | integer | -120..180 |
| Zamknięcie po zmierzchu (min) | `schedule.close_after_dusk_min` | integer | -120..180 |
| Timeout ruchu (s) | `safety.move_timeout_s` | integer | 5..120 |
| Hasło AP | `wifi.ap_password` | password | `""` or 8..63 chars (write-only) |
| Wyczyść hasło (otwarta sieć AP) | `wifi.ap_password` | checkbox | sends `""` (R39) |

- Password field is never prefilled.
  - Blank input and checkbox unchecked = keep stored password (key omitted from POST).
  - Typed value = new password (8..63 chars).
  - Checkbox `clear password (open AP)` checked = send `ap_password: ""` (open AP, R39).
    Checking it disables the password input (its value is ignored).
- Submit: copy `cfgFull`, delete `wifi.ap_password_set` (GET-only), overwrite the six fields,
  set `wifi.ap_password` only when typed or checkbox checked (checkbox wins: `""`), POST `/api/config`.
  - 200 → `Zapisano.` + reload forecast.
  - 400 → show server `error` text verbatim (e.g. `safety.move_timeout_s: 500 outside 5..120`).
- Integers: `Number.isInteger` check in JS before POST; server is the authority.
- Keys not in the form are sent back unchanged (future keys survive round trip), except
  `wifi.ap_password_set` which is always removed (server rejects it).

## 5. Size-budget test (add to `tests/test_api.py`, phase 7)

```python
import gzip

def test_index_under_size_budget():
    path = "src/www/index.html"   # CPython path (INDEX_PATH on device is /www/index.html.gz)
    with open(path, "rb") as f:
        size = len(gzip.compress(f.read(), 9))   # gzip -9 size, what gets served
    if size >= 10 * 1024:
        raise ValueError("index.html gzip -9 over 10 KB budget")
```

Failing-first: file missing → test fails. Creating the file is the fix.

## 6. Manual browser checklist

Run `uv run python run_local.py` → open `http://localhost:5000` in a phone-sized
browser window (DevTools 375 px width) and on a real phone on the AP (device stage).

- [ ] Badge shows `IDLE_CLOSED` (dev runner state) with red colour.
- [ ] Time `RTC: 12:00` visible; updates every 3 s.
- [ ] Schedule shows `HH:MM` for open and close.
- [ ] Forecast SVG draws two polylines, 14 points each.
- [ ] Otwórz → state goes `MOVING_OPEN`, then `MANUAL_HOLD_OPEN` (mock limits).
- [ ] Zamknij while MOVING_OPEN → reverses.
- [ ] Config form pre-filled from `/api/config`.
- [ ] Submit `move_timeout_s=500` → red error text `safety.move_timeout_s: 500 outside 5..120`.
- [ ] Valid save → `Zapisano.`; `config.local.json` written.
- [ ] Sync button → `Zsynchronizowano`; `/api/status` time changes.
- [ ] Reboot → confirm dialog appears; cancel does nothing.
- [ ] Browser DevTools network tab: **no request leaves localhost** (no external URL).
- [ ] Page size in DevTools: gzip -9 < 10 KB (served as /www/index.html.gz).
- [ ] Turn off network / stop server → offline banner after ~6 s.
- [ ] Forced SAFETY_STOP (dev hook or mock) → buttons disabled, exact text shown.
- [ ] Fresh config (`ap_password` empty) → red `#apwarn` banner visible.
- [ ] Save with 7-char password → red error `wifi.ap_password: must be "" or 8..63 chars`.
- [ ] Save with 8+ char password → `Zapisano.`; `#apwarn` hidden; password field empty after save.
- [ ] Save with password field blank → stored password kept (`ap_password_set` stays true).
- [ ] Tick "clear password (open AP)" + save → `Zapisano.`; `#apwarn` shown; `ap_password_set` false (R39).

## 7. VERIFY ON DEVICE

- Serve the HTML (budget gzip -9 < 10 KB (served as /www/index.html.gz), 19.5 KB raw actual) from LittleFS via `send_file` without RAM issue.
- Phone on AP: poll every 3 s with AP idle timer (touch on each request, Phase 8).
- Browser behaviour with `fetch` + `confirm` on iOS Safari / Android Chrome (captive portal not used).
- Font 16 px prevents zoom on focus (iOS) — check on device.
- Polish diacritics in served file: `<meta charset="UTF-8">` respected by microdot `send_file` (VERIFY content-type header).

## Notes

- No build, no minify. Size budget is the only size control. If over budget,
  remove code, not add tooling.
- Ponytail: no framework, no router, no state library. One `render()` function
  replaces all DOM nodes it owns.
