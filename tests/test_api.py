# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
import asyncio
import json
import sys
import types

import pytest
from microdot.test_client import TestClient

import main
import web
from state import GateState
from tests.mock_hardware import MockPin, MockRTC
from web import create_app


@pytest.fixture
def env(ctrl, tmp_path):
    e = types.SimpleNamespace()
    e.ctrl = ctrl
    e.touched = []
    e.resets = []
    e.config_path = str(tmp_path / "config.json")
    e.index_path = str(tmp_path / "index.html")
    app = create_app(
        ctrl,
        touch_cb=lambda: e.touched.append(1),
        config_path=e.config_path,
        index_path=e.index_path,
        reset_fn=lambda: e.resets.append(1),
    )
    e.client = TestClient(app)
    return e


async def _get_doc(client):
    d = (await client.get("/api/config")).json
    d["wifi"].pop("ap_password_set", None)
    d["wifi"].pop("webrepl_password_set", None)
    return d


def _set(d, section, key, value):
    d[section][key] = value
    return d


# 1-2 status
async def test_status_shape(env):
    res = await env.client.get("/api/status")
    assert res.status_code == 200
    body = res.json
    assert set(body) == {
        "state",
        "time",
        "limit_top",
        "limit_bottom",
        "config_warning",
        "today_schedule",
        "rtc_synced",
        "events",
    }
    assert body["state"] == "IDLE_CLOSED"


async def test_status_no_removed_fields(env):
    body = (await env.client.get("/api/status")).json
    for key in body:
        assert not any(w in key.lower() for w in ("lux", "light", "bh1750", "vbat", "battery"))


# 3-7 config
async def test_config_get_shape(env):
    env.ctrl.config["wifi"]["ap_password"] = "hunter22"
    res = await env.client.get("/api/config")
    body = res.json
    assert set(body) == {"location", "schedule", "safety", "wifi"}
    assert body["wifi"]["ap_password_set"] is True
    assert "ap_password" not in body["wifi"]
    assert "hunter22" not in res.text


async def test_config_post_roundtrip(env):
    env.ctrl._date = (2026, 6, 15)
    d = await _get_doc(env.client)
    d["schedule"]["open_after_sunrise_min"] = 45
    res = await env.client.post("/api/config", body=d)
    assert res.status_code == 200
    assert res.json == {"ok": True}
    got = (await env.client.get("/api/config")).json
    assert got["schedule"]["open_after_sunrise_min"] == 45
    assert env.ctrl.config["schedule"]["open_after_sunrise_min"] == 45
    with open(env.config_path, encoding="utf-8") as f:
        assert json.load(f)["schedule"]["open_after_sunrise_min"] == 45
    assert env.ctrl._date is None


async def test_config_post_keeps_password_when_absent(env):
    env.ctrl.config["wifi"]["ap_password"] = "hunter22"
    d = await _get_doc(env.client)
    assert "ap_password" not in d["wifi"]
    res = await env.client.post("/api/config", body=d)
    assert res.status_code == 200
    assert env.ctrl.config["wifi"]["ap_password"] == "hunter22"
    assert (await env.client.get("/api/config")).json["wifi"]["ap_password_set"] is True


async def test_config_get_masks_webrepl_password(env):
    env.ctrl.config["wifi"]["webrepl_password"] = "rpl4pass"
    res = await env.client.get("/api/config")
    assert "webrepl_password" not in res.json["wifi"]
    assert res.json["wifi"]["webrepl_password_set"] is True
    assert "rpl4pass" not in res.text


async def test_config_post_keeps_webrepl_password_when_absent(env):
    env.ctrl.config["wifi"]["webrepl_password"] = "rpl4pass"
    d = await _get_doc(env.client)
    assert "webrepl_password" not in d["wifi"]
    res = await env.client.post("/api/config", body=d)
    assert res.status_code == 200
    assert env.ctrl.config["wifi"]["webrepl_password"] == "rpl4pass"
    assert (await env.client.get("/api/config")).json["wifi"]["webrepl_password_set"] is True


async def test_config_post_sets_webrepl_password_when_present(env):
    env.ctrl.config["wifi"]["webrepl_password"] = "rpl4pass"
    d = await _get_doc(env.client)
    d["wifi"]["webrepl_password"] = "newrpl99"
    res = await env.client.post("/api/config", body=d)
    assert res.status_code == 200
    assert env.ctrl.config["wifi"]["webrepl_password"] == "newrpl99"


@pytest.mark.parametrize(
    "make_body,msg",
    [
        (lambda d: [], "not an object"),
        (lambda d: "{", "invalid JSON"),
        (lambda d: {**d, "foo": 1}, "unknown key foo"),
        (lambda d: _set(d, "safety", "move_timeout_s", 500), "safety.move_timeout_s"),
        (lambda d: _set(d, "safety", "move_timeout_s", True), "safety.move_timeout_s"),
        (lambda d: _set(d, "wifi", "ap_password_set", True), "unknown key ap_password_set"),
        (lambda d: _set(d, "wifi", "webrepl_password_set", True), "unknown key webrepl_password_set"),
    ],
)
async def test_config_post_400(env, make_body, msg):
    d = await _get_doc(env.client)
    res = await env.client.post("/api/config", body=make_body(d))
    assert res.status_code == 400
    assert msg in res.json["error"]


async def test_config_post_save_failure_500(env, monkeypatch):
    def boom(cfg, path):
        raise OSError("disk full")

    monkeypatch.setattr(web, "save", boom)
    d = await _get_doc(env.client)
    d["schedule"]["open_after_sunrise_min"] = 45
    res = await env.client.post("/api/config", body=d)
    assert res.status_code == 500
    assert res.json == {"error": "save failed"}
    assert env.ctrl.config["schedule"]["open_after_sunrise_min"] == 30


# 8 AP password length
@pytest.mark.parametrize("pw,status", [("", 200), ("x" * 7, 400), ("x" * 8, 200), ("x" * 64, 400)])
async def test_ap_password_validation(env, pw, status):
    d = await _get_doc(env.client)
    d["wifi"]["ap_password"] = pw
    res = await env.client.post("/api/config", body=d)
    assert res.status_code == status
    if status == 400:
        assert res.json["error"] == 'wifi.ap_password: must be "" or 8..63 printable ASCII chars'


# 9-12 time
@pytest.mark.parametrize(
    "ts,expected",
    [(1717228800000, (2024, 6, 1, 9, 0)), (1704067200000, (2024, 1, 1, 1, 0))],
)
async def test_time_utc_to_cet(env, ts, expected):
    env.ctrl._date = (2026, 6, 15)
    res = await env.client.post("/api/time", body={"timestamp_ms": ts})
    assert res.status_code == 200
    assert res.json == {"ok": True}
    assert env.ctrl.rtc.datetime()[:5] == expected
    assert env.ctrl._date is None


@pytest.mark.parametrize(
    "body,msg",
    [
        ({"timestamp_ms": "abc"}, "timestamp_ms: int required"),
        ({"timestamp_ms": 1.5}, "timestamp_ms: int required"),
        ({"timestamp_ms": True}, "timestamp_ms: int required"),
        ({}, "timestamp_ms: int required"),
        ({"timestamp_ms": 0}, "timestamp_ms: year outside 2024..2099"),
    ],
)
async def test_time_400(env, body, msg):
    res = await env.client.post("/api/time", body=body)
    assert res.status_code == 400
    assert res.json["error"] == msg


@pytest.mark.parametrize(
    "ts,status",
    [
        (1704063600000, 200),  # CET 2024-01-01 00:00
        (1704063599000, 400),  # CET 2023-12-31 23:59:59
        (4102441199000, 200),  # CET 2099-12-31 23:59:59
        (4102441200000, 400),  # CET 2100-01-01 00:00
    ],
)
async def test_time_cet_year_boundary(env, ts, status):
    res = await env.client.post("/api/time", body={"timestamp_ms": ts})
    assert res.status_code == status


async def test_time_rtc_write_failed_500(env):
    def boom(dt):
        raise OSError("i2c nack")

    env.ctrl.rtc.set_datetime = boom
    env.ctrl._date = (2026, 6, 15)
    res = await env.client.post("/api/time", body={"timestamp_ms": 1717228800000})
    assert res.status_code == 500
    assert res.json == {"error": "rtc write failed"}
    assert env.ctrl._date == (2026, 6, 15)


# 13-15 manual
async def test_manual_open_from_idle_closed(env):
    res = await env.client.post("/api/manual?action=open")
    assert res.status_code == 200
    assert res.json == {"ok": True}
    assert env.ctrl.state == GateState.MOVING_OPEN


@pytest.mark.parametrize(
    "state,path,status,msg",
    [
        (GateState.IDLE_CLOSED, "/api/manual?action=spin", 400, "invalid action"),
        (GateState.SAFETY_STOP, "/api/manual?action=open", 409, "SAFETY_STOP: clear with physical button at the coop"),
        (GateState.ERROR, "/api/manual?action=open", 409, "ERROR: reboot required"),
    ],
)
async def test_manual_refused(env, state, path, status, msg):
    env.ctrl.state = state
    res = await env.client.post(path)
    assert res.status_code == status
    assert res.json["error"] == msg
    assert env.ctrl.state == state


async def test_manual_web_refused_in_safety_stop(env):
    env.ctrl.state = GateState.SAFETY_STOP
    assert env.ctrl.manual_move("open", source="web") is False
    assert env.ctrl.state == GateState.SAFETY_STOP


# 16-17 forecast
async def test_forecast_14(env):
    for path in ("/api/forecast", "/api/forecast?days=3"):
        res = await env.client.get(path)
        rows = res.json
        assert res.status_code == 200
        assert len(rows) == 14
        assert all(len(r) == 2 and all(type(v) is int for v in r) for r in rows)


async def test_forecast_unsynced_empty(env):
    env.ctrl.rtc = MockRTC((1999, 1, 1, 0, 0, 0, 0))
    res = await env.client.get("/api/forecast")
    assert res.status_code == 200
    assert res.json == []


async def test_forecast_osf_set_returns_empty(env):
    """OSF=1 with valid 2026 time -> [] (spec 05 §2.5: time untrusted)."""
    env.ctrl.rtc = MockRTC((2026, 6, 15, 12, 0, 0, 0), osf=True)
    res = await env.client.get("/api/forecast")
    assert res.status_code == 200
    assert res.json == []


# 18 index
async def test_index_200_and_503(env):
    res = await env.client.get("/")
    assert res.status_code == 503
    assert res.json == {"error": "UI not uploaded"}
    with open(env.index_path, "w", encoding="utf-8") as f:
        f.write("<html></html>")
    res = await env.client.get("/")
    assert res.status_code == 200
    assert "text/html" in str(res.headers).lower()


async def test_index_serves_gz_when_present(env):
    gz = b"\x1f\x8b fake-gzip-bytes"
    with open(env.index_path + ".gz", "wb") as f:
        f.write(gz)
    res = await env.client.get("/")
    assert res.status_code == 200
    headers = {k.lower(): v for k, v in res.headers.items()}
    assert headers["content-encoding"] == "gzip"
    assert headers["content-type"].startswith("text/html")
    assert res.body == gz


async def test_index_plain_when_no_gz(env):
    with open(env.index_path, "w", encoding="utf-8") as f:
        f.write("<html></html>")
    res = await env.client.get("/")
    assert res.status_code == 200
    headers = {k.lower(): v for k, v in res.headers.items()}
    assert "content-encoding" not in headers


# 19 reboot
@pytest.mark.parametrize("state", [GateState.ERROR, GateState.SAFETY_STOP])
async def test_reboot_200_any_state(env, state):
    env.ctrl.state = state
    res = await env.client.post("/api/reboot")
    assert res.status_code == 200
    assert res.json == {"ok": True}
    await asyncio.sleep(0.3)
    assert env.resets == [1]


# 20-23 misc
async def test_touch_cb_called(env):
    await env.client.get("/api/status")
    assert len(env.touched) == 1


@pytest.mark.parametrize("path", ["/api/events", "/api/stream", "/api/forecast.svg", "/api/debug", "/api/nope"])
async def test_unknown_api_route_json_404(env, path):
    res = await env.client.get(path)
    assert res.status_code == 404
    assert res.json == {"error": "not found"}


# Captive portal: any non-/api/ miss -> 302 to the AP UI (phone probes, removed pages, unknown paths).
@pytest.mark.parametrize("path", ["/nope", "/generate_204", "/debug.html", "/logs.html", "/config.html"])
async def test_non_api_miss_302_captive(env, path):
    res = await env.client.get(path)
    assert res.status_code == 302
    headers = {k.lower(): v for k, v in res.headers.items()}
    assert headers["location"] == "http://192.168.4.1/"


@pytest.mark.parametrize("path", ["/api/log", "/api/log?limit=5", "/api/log?limit=abc"])
async def test_log_fixed_list(env, path):
    res = await env.client.get(path)
    assert res.status_code == 200
    assert isinstance(res.json, list)


# 24 diag
async def test_diag_route_returns_cb_dict(ctrl):
    payload = {
        "rtc": True,
        "pcf": True,
        "osf": False,
        "top": 1,
        "bottom": 0,
        "nfault": 1,
        "b_open": 1,
        "b_close": 1,
        "mhz": 80,
        "mem": 1000,
        "fs_kb": 500,
        "up_s": 7,
        "clients": None,
    }
    app = create_app(ctrl, touch_cb=lambda: None, diag_cb=lambda: payload)
    res = await TestClient(app).get("/api/diag")
    assert res.status_code == 200
    assert res.json == payload


async def test_diag_404_without_cb(env):
    res = await env.client.get("/api/diag")
    assert res.status_code == 404
    assert res.json == {"error": "not found"}


class _FakeI2C:
    def __init__(self, addrs, osf_byte=0x00):
        self._addrs = addrs
        self._osf = osf_byte

    def scan(self):
        return list(self._addrs)

    def readfrom_mem(self, addr, reg, n):
        return bytes((self._osf,))


def test_make_diag_cb_shape(monkeypatch):
    monkeypatch.setattr(main, "machine", types.SimpleNamespace(freq=lambda: 80_000_000), raising=False)
    monkeypatch.setattr(main.gc, "mem_free", lambda: 123_456, raising=False)
    monkeypatch.setattr(main.os, "statvfs", lambda p: (4096, 4096, 100, 40, 25, 0, 0, 0, 0, 0), raising=False)
    monkeypatch.setattr(main, "ticks_ms", lambda: 7_500)
    ap_off = types.SimpleNamespace(WLAN=lambda i: types.SimpleNamespace(active=lambda: False), AP_IF=1)
    monkeypatch.setitem(sys.modules, "network", ap_off)
    pins = (MockPin(1), MockPin(0), MockPin(1), MockPin(1), MockPin(0))
    cb = main.make_diag_cb(_FakeI2C([0x20, 0x68], osf_byte=0x80), *pins)
    assert cb() == {
        "rtc": True,
        "pcf": True,
        "osf": True,
        "top": 1,
        "bottom": 0,
        "nfault": 1,
        "b_open": 1,
        "b_close": 0,
        "mhz": 80,
        "mem": 120,
        "fs_kb": 100,
        "up_s": 7,
        "clients": None,
    }
    # empty bus: rtc/pcf False, osf null (RTC missing)
    empty = main.make_diag_cb(_FakeI2C([]), *pins)()
    assert (empty["rtc"], empty["pcf"], empty["osf"]) == (False, False, None)
