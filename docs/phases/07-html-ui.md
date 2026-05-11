# Phase 7 — HTML UI

## Goal

Implement the four static HTML pages. No automated tests — verify manually with `python run_local.py` and a browser.

## Prerequisites

- Phase 5 complete (all API endpoints working)
- Phase 6 complete (`/api/logs` real data)

## Deliverables

| File | Status |
|------|--------|
| `src/www/index.html` | create |
| `src/www/config.html` | create |
| `src/www/debug.html` | create |
| `src/www/logs.html` | update (created in Phase 6, enhance here) |

## Stack

- **Pico.css v2** — semantic HTML styling, zero class spam. Use CDN: `https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css`
- **HTMX 2.x** — declarative AJAX. Use CDN: `https://unpkg.com/htmx.org@2.0.0`
- **SSE extension** — `https://unpkg.com/htmx-ext-sse@2.2.1/sse.js`
- **Vanilla JS** — minimum, only for time sync button and SVG lux sparkline

No build tools. No npm. No bundler. Pure static HTML.

---

## 1. `src/www/index.html` — Status Dashboard

### What it shows

- **Gate state badge** — colored pill matching state name
- **Schedule today** — `HH:MM – HH:MM` for sensor window + backstop times
- **Warning banner** — yellow if `config_warning` non-empty
- **Lux values** — the 5 lux samples as text + inline SVG sparkline (last N readings)
- **Time** — RTC time (HH:MM)
- **Limit switches** — GÓRA / DÓŁ (active/inactive)
- **VBAT** — voltage in V
- **Manual buttons** — Open / Close → POST `/api/manual?action=open|close`
- **30-day forecast SVG** — embedded inline from API data (rendered server-side via `/`)

### SSE + fallback

Primary: SSE via HTMX extension updating status badge/lux every 2s.
Fallback: polling `/api/status` every 2s if SSE unavailable.

```html
<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Coop — Status</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
  <script src="https://unpkg.com/htmx.org@2.0.0"></script>
  <script src="https://unpkg.com/htmx-ext-sse@2.2.1/sse.js"></script>
  <style>
    .badge { display:inline-block; padding:.25em .75em; border-radius:2em; font-weight:bold; }
    .IDLE_OPEN,.MANUAL_HOLD_OPEN { background:#2a7; color:#fff; }
    .IDLE_CLOSED,.MANUAL_HOLD_CLOSED { background:#c33; color:#fff; }
    .MOVING_OPEN,.MOVING_CLOSE { background:#f90; color:#000; }
    .SAFETY_STOP { background:#f60; color:#fff; }
    .ERROR { background:#f00; color:#fff; animation:blink 0.25s step-end infinite; }
    @keyframes blink { 50% { opacity:0; } }
    .warn-banner { background:#fa0; color:#000; padding:.5em 1em; border-radius:.5em; margin-bottom:1em; }
    .lux-spark { display:inline-block; vertical-align:middle; }
  </style>
</head>
<body>
  <main class="container">
    <nav>
      <a href="/"><strong>Status</strong></a> |
      <a href="/config">Konfiguracja</a> |
      <a href="/logs">Logi</a> |
      <a href="/debug">Debug</a>
    </nav>

    <!-- SSE live status block -->
    <div id="status-block"
         hx-ext="sse"
         sse-connect="/api/events"
         sse-swap="message"
         hx-swap="none">
      <!-- Fallback: poll if SSE not available -->
      <div hx-get="/api/status"
           hx-trigger="every 2s"
           hx-swap="innerHTML"
           hx-target="#status-content">
      </div>
    </div>

    <div id="status-content"
         hx-get="/api/status"
         hx-trigger="load"
         hx-swap="innerHTML">
      <p aria-busy="true">Ładowanie…</p>
    </div>

    <script>
      // Parse SSE event and update status-content
      document.body.addEventListener("htmx:sseMessage", function(evt) {
        try {
          updateStatus(JSON.parse(evt.detail.data));
        } catch(e) {}
      });

      // Also handle hx-get polling response
      document.body.addEventListener("htmx:afterOnLoad", function(evt) {
        if (evt.detail.pathInfo && evt.detail.pathInfo.requestPath === "/api/status") {
          try {
            updateStatus(JSON.parse(evt.detail.xhr.responseText));
          } catch(e) {}
        }
      });

      function fmtMin(m) {
        if (m === null || m === undefined) return "—";
        return String(Math.floor(m/60)).padStart(2,"0") + ":" + String(m%60).padStart(2,"0");
      }

      function updateStatus(d) {
        const el = document.getElementById("status-content");
        const warn = d.config_warning
          ? `<div class="warn-banner">⚠ ${d.config_warning}</div>`
          : "";
        const sched = d.today_schedule
          ? `<p>Okno sensora: <strong>${fmtMin(d.today_schedule.wo)} – ${fmtMin(d.today_schedule.wc)}</strong> &nbsp;
             Backstop: <strong>${fmtMin(d.today_schedule.ao)}</strong> / <strong>${fmtMin(d.today_schedule.ac)}</strong></p>`
          : "";
        const luxText = d.lux ? d.lux.map(v => v.toFixed(1)).join(", ") + " lx" : "—";
        const limitTop = d.limit_top ? "✓ GÓRA" : "✗ GÓRA";
        const limitBot = d.limit_bottom ? "✓ DÓŁ" : "✗ DÓŁ";
        const vbat = d.vbat_v !== null && d.vbat_v !== undefined
          ? d.vbat_v.toFixed(2) + " V" : "—";
        el.innerHTML = `
          ${warn}
          <p>Stan: <span class="badge ${d.state}">${d.state}</span></p>
          <p>Czas RTC: <strong>${d.time || "—"}</strong></p>
          ${sched}
          <p>Lux: ${luxText}</p>
          <p>Krańcówki: ${limitTop} &nbsp; ${limitBot}</p>
          <p>Bateria RTC: ${vbat}</p>
        `;
      }
    </script>

    <!-- Manual control buttons -->
    <section>
      <h2>Sterowanie</h2>
      <button hx-post="/api/manual?action=open"
              hx-confirm="Otworzyć drzwi?"
              hx-swap="none">
        Otwórz
      </button>
      <button hx-post="/api/manual?action=close"
              hx-confirm="Zamknąć drzwi?"
              hx-swap="none"
              class="secondary">
        Zamknij
      </button>
    </section>

    <!-- 30-day forecast SVG (server-side rendered, loaded once) -->
    <section>
      <h2>Prognoza 30 dni</h2>
      <div hx-get="/api/forecast-svg" hx-trigger="load" hx-swap="innerHTML">
        <p aria-busy="true">Ładowanie wykresu…</p>
      </div>
    </section>
  </main>
</body>
</html>
```

Add `/api/forecast-svg` endpoint to `web.py`:

```python
@app.route("/api/forecast-svg")
async def forecast_svg(request):
    from src.web import _build_forecast_svg
    svg = _build_forecast_svg(ctrl.get_forecast(30))
    return Response(body=svg, headers={"Content-Type": "image/svg+xml"})
```

---

## 2. `src/www/config.html` — Configuration Editor

```html
<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Coop — Configuration</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
  <script src="https://unpkg.com/htmx.org@2.0.0"></script>
</head>
<body>
  <main class="container">
    <nav>
      <a href="/">Status</a> |
      <a href="/config"><strong>Konfiguracja</strong></a> |
      <a href="/logs">Logi</a> |
      <a href="/debug">Debug</a>
    </nav>

    <h1>Konfiguracja</h1>

    <div id="error-msg" style="color:red;display:none"></div>
    <div id="ok-msg" style="color:green;display:none">Zapisano.</div>

    <form id="cfg-form">
      <fieldset>
        <legend>Okno sensora</legend>
        <label>Tryb
          <select name="window.mode" id="window-mode">
            <option value="sun_position">Słońce (dynamiczne)</option>
            <option value="legacy">Stałe godziny</option>
          </select>
        </label>
        <div id="sun-fields">
          <label>Offset wschodu (min) <input type="number" name="window.sun.sunrise_offset_min"></label>
          <label>Offset zachodu (min) <input type="number" name="window.sun.sunset_offset_min"></label>
        </div>
        <div id="legacy-fields" style="display:none">
          <label>Godzina otwarcia <input type="number" name="window.legacy.hour_open" min="0" max="23"></label>
          <label>Godzina zamknięcia <input type="number" name="window.legacy.hour_close" min="0" max="23"></label>
        </div>
      </fieldset>

      <fieldset>
        <legend>Siłowe otwarcie (backstop)</legend>
        <label>Tryb
          <select name="override_open.mode" id="open-mode">
            <option value="dynamic">Dynamiczne (po wschodzie)</option>
            <option value="fixed">Stała godzina</option>
          </select>
        </label>
        <label>Minuty po wschodzie <input type="number" name="override_open.after_sunrise_min"></label>
        <label>Stała godzina <input type="number" name="override_open.fixed_hour" min="0" max="23"></label>
      </fieldset>

      <fieldset>
        <legend>Siłowe zamknięcie (backstop)</legend>
        <label>Tryb
          <select name="override_close.mode" id="close-mode">
            <option value="fixed">Stała godzina</option>
            <option value="dynamic">Dynamiczne (po zachodzie)</option>
          </select>
        </label>
        <label>Minuty po zachodzie <input type="number" name="override_close.after_sunset_min"></label>
        <label>Stała godzina <input type="number" name="override_close.fixed_hour" min="0" max="23"></label>
      </fieldset>

      <fieldset>
        <legend>Czujnik światła</legend>
        <label>Próg otwarcia (lux) <input type="number" step="0.5" name="light.lux_open"></label>
        <label>Próg zamknięcia (lux) <input type="number" step="0.5" name="light.lux_close"></label>
      </fieldset>

      <fieldset>
        <legend>Bezpieczeństwo</legend>
        <label>Timeout ruchu (s) <input type="number" name="safety.move_timeout_s" min="5" max="60"></label>
      </fieldset>

      <button type="submit">Zapisz</button>
    </form>

    <!-- Time sync -->
    <hr>
    <section>
      <h2>Synchronizacja Czasu</h2>
      <p>Ustawia DS3231 na podstawie czasu przeglądarki (UTC → CET).</p>
      <button id="sync-time-btn">Synchronizuj czas z przeglądarki</button>
      <span id="sync-result"></span>
    </section>

    <script>
      // Load current config and populate form
      fetch("/api/config").then(r => r.json()).then(cfg => {
        populateForm(cfg);
        toggleModeFields();
      });

      function populateForm(cfg) {
        document.querySelector("[name='window.mode']").value = cfg.window.mode;
        document.querySelector("[name='window.sun.sunrise_offset_min']").value = cfg.window.sun.sunrise_offset_min;
        document.querySelector("[name='window.sun.sunset_offset_min']").value = cfg.window.sun.sunset_offset_min;
        document.querySelector("[name='window.legacy.hour_open']").value = cfg.window.legacy.hour_open;
        document.querySelector("[name='window.legacy.hour_close']").value = cfg.window.legacy.hour_close;
        document.querySelector("[name='override_open.mode']").value = cfg.override_open.mode;
        document.querySelector("[name='override_open.after_sunrise_min']").value = cfg.override_open.after_sunrise_min;
        document.querySelector("[name='override_open.fixed_hour']").value = cfg.override_open.fixed_hour;
        document.querySelector("[name='override_close.mode']").value = cfg.override_close.mode;
        document.querySelector("[name='override_close.after_sunset_min']").value = cfg.override_close.after_sunset_min;
        document.querySelector("[name='override_close.fixed_hour']").value = cfg.override_close.fixed_hour;
        document.querySelector("[name='light.lux_open']").value = cfg.light.lux_open;
        document.querySelector("[name='light.lux_close']").value = cfg.light.lux_close;
        document.querySelector("[name='safety.move_timeout_s']").value = cfg.safety.move_timeout_s;
      }

      function toggleModeFields() {
        const wm = document.getElementById("window-mode").value;
        document.getElementById("sun-fields").style.display = wm === "sun_position" ? "" : "none";
        document.getElementById("legacy-fields").style.display = wm === "legacy" ? "" : "none";
      }
      document.getElementById("window-mode").addEventListener("change", toggleModeFields);

      function formToConfig() {
        const g = n => parseFloat(document.querySelector("[name='"+n+"']").value);
        const s = n => document.querySelector("[name='"+n+"']").value;
        return {
          window: {
            mode: s("window.mode"),
            legacy: { hour_open: g("window.legacy.hour_open"), hour_close: g("window.legacy.hour_close") },
            sun: { sunrise_offset_min: g("window.sun.sunrise_offset_min"), sunset_offset_min: g("window.sun.sunset_offset_min") }
          },
          override_open: {
            mode: s("override_open.mode"),
            fixed_hour: g("override_open.fixed_hour"),
            after_sunrise_min: g("override_open.after_sunrise_min")
          },
          override_close: {
            mode: s("override_close.mode"),
            fixed_hour: g("override_close.fixed_hour"),
            after_sunset_min: g("override_close.after_sunset_min")
          },
          light: { lux_open: g("light.lux_open"), lux_close: g("light.lux_close") },
          safety: { move_timeout_s: g("safety.move_timeout_s") }
        };
      }

      document.getElementById("cfg-form").addEventListener("submit", async function(e) {
        e.preventDefault();
        const errEl = document.getElementById("error-msg");
        const okEl = document.getElementById("ok-msg");
        errEl.style.display = "none";
        okEl.style.display = "none";
        const cfg = formToConfig();
        const res = await fetch("/api/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(cfg)
        });
        const data = await res.json();
        if (res.ok) {
          okEl.style.display = "";
        } else {
          errEl.textContent = data.error || "Błąd zapisu";
          errEl.style.display = "";
        }
      });

      document.getElementById("sync-time-btn").addEventListener("click", async function() {
        const res = await fetch("/api/time", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ timestamp_ms: Date.now() })
        });
        document.getElementById("sync-result").textContent = res.ok ? "✓ Zsynchronizowano" : "✗ Błąd";
      });
    </script>
  </main>
</body>
</html>
```

---

## 3. `src/www/debug.html` — Debug Page

```html
<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Coop — Debug</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
  <script src="https://unpkg.com/htmx.org@2.0.0"></script>
</head>
<body>
  <main class="container">
    <nav>
      <a href="/">Status</a> |
      <a href="/config">Konfiguracja</a> |
      <a href="/logs">Logi</a> |
      <a href="/debug"><strong>Debug</strong></a>
    </nav>

    <h1>Debug</h1>

    <div hx-get="/api/status" hx-trigger="load, every 5s" hx-swap="innerHTML" id="debug-data">
      <p aria-busy="true">Ładowanie…</p>
    </div>

    <script>
      document.body.addEventListener("htmx:afterOnLoad", function(evt) {
        if (evt.detail.pathInfo && evt.detail.pathInfo.requestPath === "/api/status") {
          try {
            const d = JSON.parse(evt.detail.xhr.responseText);
            document.getElementById("debug-data").innerHTML = `
              <table>
                <tr><th>Stan enum</th><td>${d.state}</td></tr>
                <tr><th>Czas RTC</th><td>${d.time}</td></tr>
                <tr><th>Lux [5]</th><td>${(d.lux||[]).map(v=>v.toFixed(2)).join(", ")}</td></tr>
                <tr><th>Krańcówka GÓRA</th><td>${d.limit_top}</td></tr>
                <tr><th>Krańcówka DÓŁ</th><td>${d.limit_bottom}</td></tr>
                <tr><th>VBAT</th><td>${d.vbat_v !== null && d.vbat_v !== undefined ? d.vbat_v.toFixed(3)+" V" : "—"}</td></tr>
                <tr><th>Config warning</th><td>${d.config_warning || "—"}</td></tr>
                <tr><th>Wersja firmware</th><td>v2.0.0-dev</td></tr>
              </table>
            `;
          } catch(e) {}
        }
      });
    </script>

    <hr>
    <section>
      <h2>WebREPL</h2>
      <p><a href="http://micropython.org/webrepl/#192.168.4.1:8266" target="_blank">
        Otwórz WebREPL (192.168.4.1:8266)
      </a></p>
    </section>

    <section>
      <h2>Reboot</h2>
      <button hx-post="/api/reboot"
              hx-confirm="Na pewno zrestartować urządzenie?"
              hx-swap="none"
              class="secondary">
        Restart
      </button>
    </section>
  </main>
</body>
</html>
```

---

## 4. Wire Static File Serving in `web.py`

Add routes for the HTML pages:

```python
@app.route("/config")
async def config_page(request):
    with open("src/www/config.html") as f:
        return Response(body=f.read(), headers={"Content-Type": "text/html"})

@app.route("/logs")
async def logs_page(request):
    with open("src/www/logs.html") as f:
        return Response(body=f.read(), headers={"Content-Type": "text/html"})

@app.route("/debug")
async def debug_page(request):
    with open("src/www/debug.html") as f:
        return Response(body=f.read(), headers={"Content-Type": "text/html"})
```

On ESP32 (Phase 8), paths change to `/www/index.html` (LittleFS). Wrap path resolution in a helper:

```python
def _html_path(name: str) -> str:
    import sys, os
    candidates = [f"src/www/{name}", f"/www/{name}"]
    for p in candidates:
        try:
            os.stat(p)
            return p
        except OSError:
            continue
    raise OSError(f"{name} not found")
```

---

## Manual Browser Checklist

Run `python run_local.py` → open `http://localhost:5000`.

### `/` Status page
- [ ] Gate state badge visible with correct color
- [ ] Time updates every 2s without page refresh
- [ ] Lux values update live
- [ ] "Harmonogram dziś" shows wo/wc/ao/ac in HH:MM format
- [ ] Yellow warning banner appears when `config_warning` is set (set it manually in run_local.py)
- [ ] 30-day SVG forecast renders (green polygon, orange dashed lines)
- [ ] Open/Close buttons POST and update state immediately

### `/config` Config page
- [ ] Form pre-populated with current config values
- [ ] Mode toggle: switching `window.mode` shows/hides sun vs legacy fields
- [ ] Valid config submits → "Zapisano." message
- [ ] Invalid config → red error message with specific field mentioned
- [ ] Time sync button sets RTC (check `/api/status` time changes)

### `/logs` Logs page
- [ ] Table renders with records from mock data
- [ ] Empty state shows "Brak zapisów."
- [ ] `open_min=null` shows "—"

### `/debug` Debug page
- [ ] All fields populated from `/api/status`
- [ ] Updates every 5s
- [ ] WebREPL link is present (doesn't need to connect)
- [ ] Reboot button shows confirmation dialog

## Notes

- CDN resources require internet. For fully offline testing (ESP32 with no internet), download Pico.css and HTMX and serve from `src/www/`. Phase 8 deployment checklist includes bundling these.
- HTMX SSE extension: verify `sse-swap="message"` attribute name in htmx-ext-sse v2.x docs — it may be `hx-ext="sse"` + `sse-connect` on parent element.
- Config form uses `name="section.field"` which plain HTML doesn't auto-nest. The JS `formToConfig()` function manually builds the nested dict. No HTMX form submit — use `fetch()` for full control.
