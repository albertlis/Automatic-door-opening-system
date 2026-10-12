# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
import asyncio
import json
import os
import sys

from microdot import Microdot, Request, send_file

from astro import civil_from_days
from compat import sleep_ms
from config import ConfigError, save, validate_config
from logs import flush, log_json
from state import GateState

Request.max_content_length = 2048  # ~2 KB body cap (decision 5); 413 VERIFY on device
IS_MICROPYTHON = sys.implementation.name == "micropython"
CONFIG_PATH = "/config.json" if IS_MICROPYTHON else "config.local.json"
INDEX_PATH = "/www/index.html" if IS_MICROPYTHON else "src/www/index.html"
FORECAST_DAYS = 14  # fixed, no query param (R26)
LOG_LIMIT = 50  # fixed, no query param (R26)
EVENTS_LIMIT = 5  # status "events" tail (R2)
TIME_YEAR_MIN = 2024  # civil year of CET time, checked after +1 h (R33)
TIME_YEAR_MAX = 2099

_BAD = object()  # sentinel: body is not valid JSON


def _json_body(request):
    # Parse raw bytes ourselves: independent of Content-Type, any failure -> _BAD.
    try:
        return json.loads(request.body.decode())
    except Exception:  # ValueError / UnicodeError -> caller answers 400
        return _BAD


def _default_reset():
    if IS_MICROPYTHON:
        import machine

        machine.reset()


def create_app(
    ctrl, touch_cb, config_path: str = CONFIG_PATH, index_path: str = INDEX_PATH, reset_fn=None, diag_cb=None
) -> Microdot:
    app = Microdot()
    if reset_fn is None:
        reset_fn = _default_reset

    @app.before_request
    async def _touch(request):
        touch_cb()

    @app.errorhandler(404)
    async def _not_found(request):
        if request.path.startswith("/api/"):
            return {"error": "not found"}, 404
        # Captive portal: phone probes (/generate_204, /hotspot-detect.html, ...) land here -> open the UI.
        return "", 302, {"Location": "http://192.168.4.1/"}

    @app.get("/")
    async def api_index(request):
        try:
            os.stat(index_path + ".gz")
        except OSError:
            pass  # no .gz -> plain file below
        else:
            return send_file(index_path + ".gz", compressed=True)
        try:
            os.stat(index_path)
        except OSError:
            return {"error": "UI not uploaded"}, 503
        return send_file(index_path)

    @app.get("/api/status")
    async def api_status(request):
        s = json.loads(ctrl.status_json())
        s["events"] = log_json(EVENTS_LIMIT)
        return s

    @app.get("/api/config")
    async def api_config_get(request):
        d = {k: dict(v) for k, v in ctrl.config.items()}
        pw = d["wifi"].pop("ap_password")
        d["wifi"]["ap_password_set"] = pw != ""
        wr = d["wifi"].pop("webrepl_password")
        d["wifi"]["webrepl_password_set"] = wr != ""
        return d

    @app.post("/api/config")
    async def api_config_post(request):
        d = _json_body(request)
        if d is _BAD:
            return {"error": "invalid JSON"}, 400
        # Absent *_password = keep stored one (R6). Done before validate so the copy is validated too.
        if isinstance(d, dict) and isinstance(d.get("wifi"), dict):
            for key in ("ap_password", "webrepl_password"):
                if key not in d["wifi"]:
                    d["wifi"][key] = ctrl.config["wifi"][key]
        try:
            validate_config(d)
        except ConfigError as e:
            return {"error": str(e)}, 400
        try:
            save(d, config_path)
        except OSError:
            return {"error": "save failed"}, 500
        ctrl.config = d
        ctrl._date = None  # ponytail: private attr, one caller. invalidate_schedule() when a second appears
        return {"ok": True}

    @app.post("/api/time")
    async def api_time(request):
        b = _json_body(request)
        ts = b.get("timestamp_ms") if isinstance(b, dict) else None
        if type(ts) is not int:
            return {"error": "timestamp_ms: int required"}, 400
        cet_s = ts // 1000 + 3600  # UTC -> CET; DST never stored (R33)
        days, sod = divmod(cet_s, 86400)
        y, m, d = civil_from_days(days)
        if not TIME_YEAR_MIN <= y <= TIME_YEAR_MAX:
            return {"error": "timestamp_ms: year outside 2024..2099"}, 400
        h, rem = divmod(sod, 3600)
        mi, s = divmod(rem, 60)
        wd = (days + 3) % 7  # Mon=0; 1970-01-01 was Thursday
        try:
            ctrl.rtc.set_datetime((y, m, d, h, mi, s, wd))
        except OSError:
            return {"error": "rtc write failed"}, 500
        ctrl._date = None
        return {"ok": True}

    @app.post("/api/manual")
    async def api_manual(request):
        action = request.args.get("action", "")
        if action not in ("open", "close"):
            return {"error": "invalid action"}, 400
        if ctrl.state == GateState.SAFETY_STOP:
            return {"error": "SAFETY_STOP: clear with physical button at the coop"}, 409
        if ctrl.state == GateState.ERROR:
            return {"error": "ERROR: reboot required"}, 409
        ctrl.manual_move(action, source="web")  # bool ignored by web (R1)
        return {"ok": True}

    @app.get("/api/forecast")
    async def api_forecast(request):
        return [[o, c] for o, c in ctrl.forecast(FORECAST_DAYS)]

    @app.get("/api/log")
    async def api_log(request):
        return log_json(LOG_LIMIT)

    @app.post("/api/reboot")
    async def api_reboot(request):
        async def _reset_soon():
            await sleep_ms(200)
            if IS_MICROPYTHON:
                flush()  # R29: ring to flash before reset (CPython: never touch /logs.bin)
            reset_fn()

        asyncio.create_task(_reset_soon())  # respond first, reset later
        return {"ok": True}

    @app.get("/api/diag")
    async def api_diag(request):
        if diag_cb is None:
            return {"error": "not found"}, 404
        return diag_cb()

    return app
