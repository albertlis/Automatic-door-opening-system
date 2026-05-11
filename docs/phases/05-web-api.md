# Phase 5 — Web API + Forecast + SVG

## Goal

Implement the microdot REST API, SSE endpoint, SVG forecast chart, and `run_local.py`. Full test coverage on CPython. Browser-accessible at `localhost:5000`.

## Prerequisites

- Phase 3 complete (`src/state.py`, `CoopController.status_json()`, `get_forecast()`)
- Phase 4 complete (`src/main.py`, async tasks)

## Deliverables

| File | Status |
|------|--------|
| `src/web.py` | create |
| `run_local.py` | create |
| `tests/test_api.py` | create |

HTML pages (`src/www/`) are created in Phase 7. Phase 5 serves stub HTML responses where needed.

## TDD Protocol

**Write `tests/test_api.py` first. All tests must fail. Then implement `src/web.py` until all pass.**

---

## 1. `src/web.py`

### Structure

```python
from microdot import Microdot, Response
import json

def create_app(ctrl) -> Microdot:
    app = Microdot()

    # Middleware: touch HTTP timestamp for AP watchdog (Phase 8 uses this)
    @app.before_request
    async def _touch(request):
        import src.main as m
        from compat import ticks_ms
        m._last_http_ms = ticks_ms()

    # ... routes ...

    return app
```

### Routes

#### `GET /api/status`

Returns JSON matching `CoopController.status_json()` plus structured fields:

```python
@app.route("/api/status")
async def api_status(request):
    y, mo, d, h, minute, *_ = ctrl.rtc.datetime()
    return Response(
        body=json.dumps({
            "state": ctrl.state.value,
            "lux": ctrl.lux_buffer,
            "time": f"{h:02d}:{minute:02d}",
            "limit_top": ctrl.limit_top.value() == 0,
            "limit_bottom": ctrl.limit_bottom.value() == 0,
            "vbat_v": None,    # Phase 8
            "config_warning": ctrl._config_warning,
            "today_schedule": {
                "wo": ctrl._today_times[0],
                "wc": ctrl._today_times[1],
                "ao": ctrl._today_times[2],
                "ac": ctrl._today_times[3],
            },
        }),
        headers={"Content-Type": "application/json"},
    )
```

#### `GET /api/events` (SSE)

```python
@app.route("/api/events")
async def api_events(request):
    import asyncio

    async def stream():
        while True:
            yield f"data: {ctrl.status_json()}\n\n"
            await asyncio.sleep(2)

    return Response(
        body=stream(),
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
```

#### `GET /api/config`

```python
@app.route("/api/config")
async def get_config(request):
    from src.config import save
    import io
    # Serialize config to dict and return as JSON
    from src.config import _to_dict  # or use dataclasses.asdict
    return Response(
        body=json.dumps(_to_dict(ctrl.config), indent=2),
        headers={"Content-Type": "application/json"},
    )
```

#### `POST /api/config`

```python
@app.route("/api/config", methods=["POST"])
async def post_config(request):
    from src.config import _from_dict, save, validate_config, ConfigError
    from src.astro import sun_times_cet, is_dst

    try:
        data = request.json
        new_cfg = _from_dict(data)
        y, mo, d, h, minute, *_ = ctrl.rtc.datetime()
        from src.astro import local_minutes
        now = local_minutes(y, mo, d, h, minute)
        rise, sset = sun_times_cet(y, mo, d)
        dst = 60 if is_dst(y, mo, d, h) else 0
        validate_config(new_cfg, now, rise, sset, dst)
        ctrl.config = new_cfg
        save(new_cfg, "/config.json")
        ctrl._resolve_times(y, mo, d, h, minute)
        return Response(body='{"ok": true}', headers={"Content-Type": "application/json"})
    except ConfigError as e:
        return Response(
            body=json.dumps({"error": str(e)}),
            status_code=400,
            headers={"Content-Type": "application/json"},
        )
```

#### `GET /api/logs` (stub — Phase 6 replaces)

```python
@app.route("/api/logs")
async def api_logs(request):
    return Response(body="[]", headers={"Content-Type": "application/json"})
```

#### `GET /api/forecast`

```python
@app.route("/api/forecast")
async def api_forecast(request):
    days = int(request.args.get("days", 30))
    days = min(days, 90)
    forecast = ctrl.get_forecast(days)
    return Response(
        body=json.dumps([[wo, wc, ao, ac] for wo, wc, ao, ac in forecast]),
        headers={"Content-Type": "application/json"},
    )
```

#### `POST /api/time`

```python
@app.route("/api/time", methods=["POST"])
async def api_time(request):
    data = request.json
    ts_ms = data["timestamp_ms"]
    # UTC ms → CET (UTC+1): always +1h, DST handled in logic layer only
    ts_s = ts_ms // 1000
    import time
    cet_s = ts_s + 3600  # UTC+1
    t = time.gmtime(cet_s)
    # t = (year, month, mday, hour, minute, second, weekday, yearday)
    ctrl.rtc.set_datetime((t[0], t[1], t[2], t[3], t[4], t[5], t[6], t[7]))
    y, mo, d, h, minute, *_ = ctrl.rtc.datetime()
    ctrl._resolve_times(y, mo, d, h, minute)
    return Response(body='{"ok": true}', headers={"Content-Type": "application/json"})
```

#### `POST /api/manual`

```python
@app.route("/api/manual", methods=["POST"])
async def api_manual(request):
    action = request.args.get("action", "")
    if action not in ("open", "close"):
        return Response(body='{"error": "invalid action"}', status_code=400,
                        headers={"Content-Type": "application/json"})
    ctrl.manual_move(action)
    return Response(body='{"ok": true}', headers={"Content-Type": "application/json"})
```

#### `POST /api/reboot`

```python
@app.route("/api/reboot", methods=["POST"])
async def api_reboot(request):
    import sys
    if sys.implementation.name == "micropython":
        import machine
        machine.reset()
    return Response(body='{"ok": true}', headers={"Content-Type": "application/json"})
```

#### `GET /` — Status page with embedded SVG

```python
@app.route("/")
async def index(request):
    try:
        with open("src/www/index.html") as f:
            html = f.read()
        return Response(body=html, headers={"Content-Type": "text/html"})
    except OSError:
        # Phase 5: serve minimal inline HTML with SVG
        svg = _build_forecast_svg(ctrl.get_forecast(30))
        return Response(
            body=f"<html><body>{svg}</body></html>",
            headers={"Content-Type": "text/html"},
        )
```

#### Static file serving

```python
@app.route("/www/<path>")
async def static(request, path):
    try:
        with open(f"src/www/{path}", "rb") as f:
            data = f.read()
        content_type = "text/css" if path.endswith(".css") else "application/javascript"
        return Response(body=data, headers={"Content-Type": content_type})
    except OSError:
        return Response(body="Not found", status_code=404)
```

### SVG Generation (`_build_forecast_svg`)

```python
def _build_forecast_svg(forecast: list[tuple]) -> str:
    """Generate 30-day forecast SVG. (§6.2)
    viewBox: 0 0 300 160
    Margins: left=30px (Y labels), bottom=16px (X labels), top=4px
    X: day 0-29 → px 30 + day * (270/29)
    Y: minutes 0-1440 → px 4 + minutes * (140/1440)  (00:00=top, 24:00=bottom)
    """
    def x(i: int) -> float:
        return 30 + i * (270 / 29)

    def y(minutes: int) -> float:
        return 4 + minutes * (140 / 1440)

    n = len(forecast)

    # Sensor window polygon (green)
    top_pts = " ".join(f"{x(i):.1f},{y(d[0]):.1f}" for i, d in enumerate(forecast))
    bot_pts = " ".join(f"{x(i):.1f},{y(d[1]):.1f}" for i, d in reversed(list(enumerate(forecast))))
    window_poly = f'<polygon points="{top_pts} {bot_pts}" fill="rgba(80,200,80,0.30)"/>'

    # Night background polygons (two: 00:00→wo and wc→24:00)
    night_top_pts = " ".join(f"{x(i):.1f},{y(4):.1f}" for i in range(n))  # y(0)=top=4px
    night_top_bot = " ".join(f"{x(i):.1f},{y(d[0]):.1f}" for i, d in reversed(list(enumerate(forecast))))
    night_bot_pts = " ".join(f"{x(i):.1f},{y(d[1]):.1f}" for i, d in enumerate(forecast))
    night_bot_end = " ".join(f"{x(i):.1f},{y(1440):.1f}" for i in reversed(range(n)))  # y(1440)=bottom
    night_poly = (
        f'<polygon points="{night_top_pts} {night_top_bot}" fill="rgba(30,30,60,0.25)"/>'
        f'<polygon points="{night_bot_pts} {night_bot_end}" fill="rgba(30,30,60,0.25)"/>'
    )

    # Backstop lines (orange dashed)
    ao_pts = " ".join(f"{x(i):.1f},{y(d[2]):.1f}" for i, d in enumerate(forecast))
    ac_pts = " ".join(f"{x(i):.1f},{y(d[3]):.1f}" for i, d in enumerate(forecast))
    backstop = (
        f'<polyline points="{ao_pts}" fill="none" stroke="#f90" stroke-width="1" stroke-dasharray="3,2"/>'
        f'<polyline points="{ac_pts}" fill="none" stroke="#f90" stroke-width="1" stroke-dasharray="3,2"/>'
    )

    # Today marker
    today_line = f'<line x1="{x(0):.1f}" y1="4" x2="{x(0):.1f}" y2="144" stroke="#fff" opacity="0.4"/>'

    # Y axis labels (every 6h: 00/06/12/18/24)
    y_labels = ""
    for h in range(0, 25, 6):
        yp = y(h * 60)
        y_labels += f'<text x="28" y="{yp:.1f}" text-anchor="end" fill="#888" font-size="8">{h:02d}</text>'

    # X axis labels (+0/+7/+14/+21/+28)
    x_labels = ""
    for step in range(0, 29, 7):
        xp = x(step)
        x_labels += f'<text x="{xp:.1f}" y="158" text-anchor="middle" fill="#888" font-size="8">+{step}</text>'

    return (
        '<svg viewBox="0 0 300 160" xmlns="http://www.w3.org/2000/svg" '
        'style="background:#111;width:100%;max-width:600px">'
        f"{night_poly}{window_poly}{backstop}{today_line}{y_labels}{x_labels}"
        "</svg>"
    )
```

---

## 2. `run_local.py`

```python
"""Run web UI locally on CPython for development/testing."""
import asyncio
from tests.mock_hardware import MockMotor, MockRTC, MockLightSensor, MockButton, MockLED, MockPCF, MockNSleep, MockNFault
from src.state import CoopController, GateState
from src.config import default_config
from src.web import create_app
import src.main as main_module

def make_ctrl() -> CoopController:
    cfg = default_config()
    cfg.window.mode = "legacy"
    c = CoopController(
        motor=MockMotor(), rtc=MockRTC(), light_sensor=MockLightSensor(),
        pcf=MockPCF(), limit_top=MockButton(), limit_bottom=MockButton(active=True),
        btn_open=MockButton(), btn_close=MockButton(),
        leds=(MockLED(), MockLED(), MockLED()),
        nsleep=MockNSleep(), nfault=MockNFault(),
        config=cfg,
    )
    c.state = GateState.IDLE_CLOSED
    c.lux_buffer = [5.0, 6.0, 7.0, 5.5, 6.5]
    c.lux_ready = True
    return c

async def run():
    ctrl = make_ctrl()
    main_module.ctrl = ctrl
    app = create_app(ctrl)
    print("Running at http://localhost:5000")
    await app.start_server(host="localhost", port=5000, debug=True)

if __name__ == "__main__":
    asyncio.run(run())
```

---

## 3. `tests/test_api.py`

```python
import pytest
import json
from src.state import CoopController, GateState
from src.web import create_app

# Use conftest ctrl fixture (from Phase 3)

@pytest.fixture
def client(ctrl):
    app = create_app(ctrl)
    return app.test_client()

# --- /api/status ---

@pytest.mark.asyncio
async def test_api_status_shape(client):
    res = await client.get("/api/status")
    assert res.status_code == 200
    data = res.json
    assert "state" in data
    assert "lux" in data
    assert len(data["lux"]) == 5
    assert "time" in data
    assert "limit_top" in data
    assert "limit_bottom" in data
    assert "config_warning" in data

@pytest.mark.asyncio
async def test_api_status_state_value(client, ctrl):
    ctrl.state = GateState.IDLE_CLOSED
    res = await client.get("/api/status")
    assert res.json["state"] == "IDLE_CLOSED"

# --- /api/time ---

@pytest.mark.asyncio
async def test_api_time_utc_to_cet(client, ctrl):
    """2024-06-01 08:00:00 UTC → RTC stores 09:00:00 CET (§8.5 exact)."""
    ts = 1717228800000   # 2024-06-01 08:00:00 UTC in ms
    res = await client.post("/api/time", json={"timestamp_ms": ts})
    assert res.status_code == 200
    dt = ctrl.rtc.datetime()
    assert dt[3] == 9   # hour = 09 (CET = UTC+1)

@pytest.mark.asyncio
async def test_api_time_midnight_utc_stores_1am_cet(client, ctrl):
    """2024-01-01 00:00:00 UTC → RTC 01:00:00 CET."""
    ts = 1704067200000   # 2024-01-01 00:00:00 UTC
    await client.post("/api/time", json={"timestamp_ms": ts})
    dt = ctrl.rtc.datetime()
    assert dt[0] == 2024
    assert dt[1] == 1
    assert dt[2] == 1
    assert dt[3] == 1   # 01:00 CET

# --- /api/config GET ---

@pytest.mark.asyncio
async def test_api_config_get_returns_json(client):
    res = await client.get("/api/config")
    assert res.status_code == 200
    data = res.json
    assert "window" in data
    assert "light" in data
    assert "safety" in data

# --- /api/config POST ---

@pytest.mark.asyncio
async def test_api_config_post_valid(client, ctrl):
    """Valid config saves and updates ctrl.config."""
    from src.config import default_config, _to_dict
    cfg_dict = _to_dict(default_config())
    cfg_dict["light"]["lux_open"] = 12.0
    res = await client.post("/api/config", json=cfg_dict)
    assert res.status_code == 200
    assert ctrl.config.light.lux_open == pytest.approx(12.0)

@pytest.mark.asyncio
async def test_api_config_post_invalid_returns_400(client):
    """Invalid config invariant → 400 with error message."""
    from src.config import default_config, _to_dict
    cfg_dict = _to_dict(default_config())
    cfg_dict["window"]["mode"] = "legacy"
    cfg_dict["window"]["legacy"]["hour_open"] = 10   # wo=600
    cfg_dict["override_open"]["mode"] = "fixed"
    cfg_dict["override_open"]["fixed_hour"] = 6      # ao=360 < wo=600 → invalid
    res = await client.post("/api/config", json=cfg_dict)
    assert res.status_code == 400
    data = res.json
    assert "error" in data
    assert "abs_open" in data["error"]

# --- /api/forecast ---

@pytest.mark.asyncio
async def test_api_forecast_default_30_days(client):
    res = await client.get("/api/forecast")
    assert res.status_code == 200
    data = res.json
    assert len(data) == 30
    assert len(data[0]) == 4  # [wo, wc, ao, ac]

@pytest.mark.asyncio
async def test_api_forecast_custom_days(client):
    res = await client.get("/api/forecast?days=7")
    assert len(res.json) == 7

@pytest.mark.asyncio
async def test_api_forecast_capped_at_90(client):
    res = await client.get("/api/forecast?days=200")
    assert len(res.json) == 90

@pytest.mark.asyncio
async def test_api_forecast_values_are_clamped(client, ctrl):
    """Forecast uses same clamp logic as _resolve_times."""
    data = (await client.get("/api/forecast")).json
    for entry in data:
        wo, wc, ao, ac = entry
        assert ao >= wo, "abs_open must be >= window_open (clamped)"
        assert ac >= wc, "abs_close must be >= window_close (clamped)"

# --- SVG generation ---

@pytest.mark.asyncio
async def test_svg_contains_required_elements(client):
    res = await client.get("/")
    assert res.status_code == 200
    body = res.body.decode() if isinstance(res.body, bytes) else res.body
    assert "<polygon" in body
    assert "<polyline" in body
    assert "<text" in body
    assert "viewBox" in body

# --- /api/manual ---

@pytest.mark.asyncio
async def test_api_manual_open(client, ctrl):
    ctrl.state = GateState.IDLE_CLOSED
    res = await client.post("/api/manual?action=open")
    assert res.status_code == 200
    assert ctrl.state == GateState.MOVING_OPEN

@pytest.mark.asyncio
async def test_api_manual_invalid_action(client):
    res = await client.post("/api/manual?action=spin")
    assert res.status_code == 400

# --- /api/logs (stub) ---

@pytest.mark.asyncio
async def test_api_logs_returns_empty_list(client):
    res = await client.get("/api/logs")
    assert res.status_code == 200
    assert res.json == []
```

---

## Acceptance Criteria

```bash
uv run pytest tests/test_api.py -v   # all green
uv run ruff check src/web.py run_local.py tests/test_api.py
# Manual: python run_local.py → open http://localhost:5000
# Verify: state badge visible, SVG chart renders, /api/status returns JSON
```

## Notes

- `microdot.test_client()` is synchronous (returns awaitable). Check microdot ≥2.0.6 docs for exact test client API — it may be `app.test_client()` returning a context manager. Adjust tests accordingly.
- `Response(body=stream(), ...)` for SSE: microdot 2.x supports async generator responses. Verify in microdot docs.
- SVG `y(0)` = `4 + 0 * (140/1440) = 4` (top). `y(1440)` = `4 + 140 = 144` (bottom). The bottom margin is `160 - 144 = 16px` for X axis labels.
- `night_top_pts` uses hardcoded `y=4` (top of chart). The variable `y(4)` would be wrong — it should be the chart top constant `4`, not `y(4 minutes)`. Use literal `4` for the constant.
- `POST /api/time` uses `time.gmtime()` (CPython). MicroPython has `utime.gmtime()` — same API, so `from compat import ...` not needed here; `import time; time.gmtime()` works on both.
- `/config.json` path in `save()` — on CPython this writes to project root. Phase 8 changes this to `/config.json` on LittleFS. For tests, mock `save` or use `tmp_path`.
