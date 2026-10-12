# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
# Phase 4 tests for src/main.py (docs/phases/04-async-tasks.md, numbering as in that doc).
# Virtual clock: sleep_ms waits on futures, advance() jumps between wake times. No real sleeps.
import asyncio
import sys
import types

import pytest

import logs
import main
import state
from config import ConfigError, default_config, validate_config
from state import GateState
from tests.mock_hardware import FakeMachine, FakeWdt, MockPin, MockRTC, make_ctrl


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    # Stub for src/dns.py: real serve_dns would bind UDP port 53. Records start and cancel.
    fake = types.SimpleNamespace(starts=0, cancelled=0)

    async def serve_dns(ip="192.168.4.1"):
        fake.starts += 1
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            fake.cancelled += 1
            raise

    mod = types.ModuleType("dns")
    mod.serve_dns = serve_dns
    monkeypatch.setitem(sys.modules, "dns", mod)
    return fake


@pytest.fixture
def fake_webrepl(monkeypatch):
    # Stub for MicroPython webrepl (firmware module, absent on CPython). Records start(password=) and stop().
    fake = types.SimpleNamespace(starts=[], stops=0, fail=False)

    def start(password=None):
        if fake.fail:
            raise OSError("bind")
        fake.starts.append(password)

    def stop():
        fake.stops += 1

    mod = types.ModuleType("webrepl")
    mod.start = start
    mod.stop = stop
    monkeypatch.setitem(sys.modules, "webrepl", mod)
    return fake


NIGHT = (2026, 6, 15, 22, 0, 0, 0)  # 22:00 CET -> local 23:00, fixed schedule (360, 1080) = closed
DAY_9AM = (2026, 6, 15, 9, 0, 0, 0)  # local 10:00 -> open


class Clock:
    def __init__(self):
        self.now = 0
        self._waiters = []

    def ticks_ms(self):
        return self.now

    async def sleep_ms(self, ms):
        fut = asyncio.get_running_loop().create_future()
        self._waiters.append((self.now + ms, fut))
        await fut

    async def advance(self, ms):
        end = self.now + ms
        while True:
            for _ in range(10):  # let woken tasks run until they block again
                await asyncio.sleep(0)
            due = [w for w in self._waiters if w[0] <= self.now]
            if due:
                self._waiters = [w for w in self._waiters if w[0] > self.now]
                for _, f in due:
                    if not f.done():
                        f.set_result(None)
                continue
            pend = [w[0] for w in self._waiters if w[0] <= end]
            if not pend:
                self.now = end
                return
            self.now = min(pend)


class RecLed:
    def __init__(self, fail=False):
        self.fail = fail
        self.writes = []

    def value(self, v):
        if self.fail:
            raise OSError("pcf write failed")
        self.writes.append(v)


class BrokenRtc(MockRTC):
    def datetime(self):
        raise OSError("i2c")


class FakeWifi:
    def __init__(self):
        self.on_calls = []
        self.off_calls = 0

    def ap_on(self, ssid, password):
        self.on_calls.append((ssid, password))

    def ap_off(self):
        self.off_calls += 1


class FakeApp:
    def __init__(self, touch, fail=False):
        self.touch = touch
        self.fail = fail
        self.shutdowns = 0
        self._stop = asyncio.Event()

    async def start_server(self, port):
        if self.fail:
            raise RuntimeError("bind failed")
        await self._stop.wait()

    def shutdown(self):
        self.shutdowns += 1
        self._stop.set()


def make_factory(apps, fail=False):
    def factory(ctrl, touch):
        app = FakeApp(touch, fail)
        apps.append(app)
        return app

    return factory


def record_manual(ctrl):
    calls = []

    def rec(action, source):
        calls.append((action, source))
        return True

    ctrl.manual_move = rec
    return calls


def btns_up():
    return (MockPin(1), MockPin(1))


def fresh_ctrl(state_name=GateState.IDLE_CLOSED, **kw):
    kw.setdefault("rtc", MockRTC(dt=NIGHT))
    ctrl = make_ctrl(**kw)
    ctrl.state = state_name
    return ctrl


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(state, "schedule", lambda cfg, y, m, d: (360, 1080))
    for mod in (state, main):
        monkeypatch.setattr(mod, "ticks_ms", c.ticks_ms)
        monkeypatch.setattr(mod, "sleep_ms", c.sleep_ms)
    return c


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    for name in ("ap_request", "ap_active", "ap_auto_done"):
        monkeypatch.setattr(main, name, False)
    monkeypatch.setattr(main, "LIGHTSLEEP_OK", False)


@pytest.fixture(autouse=True)
async def reap():
    yield
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------- control_loop


async def test_loop_ticks_and_feeds_wdt_each_iteration(clock):  # #1
    ctrl = fresh_ctrl(GateState.MOVING_OPEN)
    ticks = []
    orig_tick = ctrl.tick

    def counted():
        ticks.append(1)
        orig_tick()

    ctrl.tick = counted
    wdt = FakeWdt()
    asyncio.create_task(main.control_loop(ctrl, wdt, btns_up(), FakeMachine()))
    await clock.advance(5999)  # iterations at 0 s, 2 s, 4 s
    assert len(ticks) == 3
    assert wdt.feeds == 3


GATE_CASES = [
    ("all_gates_pass", {}, True),
    ("moving", {"state": GateState.MOVING_OPEN}, False),
    ("init", {"state": GateState.INIT}, False),
    ("safety_stop", {"state": GateState.SAFETY_STOP}, False),
    ("error", {"state": GateState.ERROR}, False),
    ("ap_active", {"ap_active": True}, False),
    ("ap_request", {"ap_request": True}, False),
    ("button_held", {"btn_low": True}, False),
    ("rtc_never_read", {"rtc": BrokenRtc(dt=NIGHT)}, False),
    ("rtc_unsynced", {"rtc": MockRTC(dt=(2019, 1, 1, 0, 0, 0, 0))}, False),
    ("flag_off", {"flag": False}, False),
]


@pytest.mark.parametrize("name, over, expect", GATE_CASES, ids=[c[0] for c in GATE_CASES])
async def test_lightsleep_only_when_every_gate_passes(clock, monkeypatch, name, over, expect):  # #2
    monkeypatch.setattr(main, "LIGHTSLEEP_OK", over.get("flag", True))
    if "ap_active" in over:
        monkeypatch.setattr(main, "ap_active", True)
    if "ap_request" in over:
        monkeypatch.setattr(main, "ap_request", True)
    ctrl = fresh_ctrl(over.get("state", GateState.IDLE_CLOSED), rtc=over.get("rtc", MockRTC(dt=NIGHT)))
    btns = (MockPin(0), MockPin(1)) if over.get("btn_low") else btns_up()
    machine_mod = FakeMachine()
    asyncio.create_task(main.control_loop(ctrl, FakeWdt(), btns, machine_mod))
    await clock.advance(10)
    assert (("lightsleep", 2000) in machine_mod.calls) is expect
    if not expect:
        assert clock._waiters  # fell back to sleep_ms(2000)


async def test_loop_never_creates_run_move(clock, monkeypatch):  # #3
    ctrl = fresh_ctrl(GateState.IDLE_CLOSED, rtc=MockRTC(dt=DAY_9AM))  # desired open -> one move
    asyncio.create_task(main.control_loop(ctrl, FakeWdt(), btns_up(), FakeMachine()))
    created = []
    real_create = asyncio.create_task

    def rec(coro, **kw):
        created.append(coro.__name__)
        return real_create(coro, **kw)

    monkeypatch.setattr(asyncio, "create_task", rec)
    await clock.advance(6000)
    assert created == ["_run_move"]  # only _start_move spawns it


@pytest.mark.parametrize("kind", ["unsynced", "osf"])
async def test_auto_ap_once_per_boot_on_unsynced_or_osf(clock, kind):  # #4
    rtc = MockRTC(dt=(2019, 1, 1, 0, 0, 0, 0)) if kind == "unsynced" else MockRTC(osf=True)
    ctrl = fresh_ctrl(GateState.IDLE_CLOSED, rtc=rtc)
    asyncio.create_task(main.control_loop(ctrl, FakeWdt(), btns_up(), FakeMachine()))
    await clock.advance(2000)
    assert main.ap_request is True
    assert main.ap_auto_done is True
    main.ap_request = False  # AP ended
    await clock.advance(20_000)
    assert main.ap_request is False  # no second auto request this boot


async def test_auto_ap_not_requested_when_rtc_never_read(clock):  # #5
    ctrl = fresh_ctrl(GateState.IDLE_CLOSED, rtc=BrokenRtc(dt=NIGHT))
    asyncio.create_task(main.control_loop(ctrl, FakeWdt(), btns_up(), FakeMachine()))
    await clock.advance(1999)  # one failed read only (3 in a row would ERROR)
    assert ctrl.rtc_synced is None
    assert main.ap_request is False


# ---------------------------------------------------------------- button_task


async def test_release_under_1s_calls_manual_move_on_release(clock):  # #7
    ctrl = fresh_ctrl()
    calls = record_manual(ctrl)
    pin, other = MockPin(1), MockPin(1)
    asyncio.create_task(main.button_task(ctrl, pin, other, "open"))
    await clock.advance(100)
    pin.value(0)  # press
    await clock.advance(200)
    assert calls == []  # held 200 ms: no action on press
    await clock.advance(100)
    assert calls == []  # held 300 ms: still no action
    pin.value(1)  # release
    await clock.advance(100)
    assert calls == [("open", "button")]


async def test_hold_1s_or_longer_no_action_incl_bounced_release(clock):  # #8
    ctrl = fresh_ctrl()
    calls = record_manual(ctrl)
    pin = MockPin(1)
    asyncio.create_task(main.button_task(ctrl, pin, MockPin(1), "open"))
    await clock.advance(100)
    pin.value(0)
    await clock.advance(1200)
    assert calls == []
    pin.value(1)  # release, bounces low again (R34)
    await clock.advance(20)
    pin.value(0)
    await clock.advance(10)
    pin.value(1)
    await clock.advance(200)
    assert calls == []


async def test_debounce_ignores_glitch_and_release_bounce(clock):  # #9
    ctrl = fresh_ctrl()
    calls = record_manual(ctrl)
    pin = MockPin(1)
    asyncio.create_task(main.button_task(ctrl, pin, MockPin(1), "open"))
    await clock.advance(100)
    pin.value(0)  # 20 ms glitch
    await clock.advance(20)
    pin.value(1)
    await clock.advance(100)
    assert calls == []
    pin.value(0)  # real press 300 ms
    await clock.advance(300)
    pin.value(1)  # release, high 20 ms, low 20 ms, high
    await clock.advance(20)
    pin.value(0)
    await clock.advance(20)
    pin.value(1)
    await clock.advance(200)
    assert calls == [("open", "button")]


@pytest.mark.parametrize("hold, expect_ap", [(2000, False), (3200, True)])
async def test_both_held_3s_sets_ap_request_no_move(clock, hold, expect_ap):  # #10
    ctrl = fresh_ctrl()
    calls = record_manual(ctrl)
    bo, bc = MockPin(1), MockPin(1)
    asyncio.create_task(main.button_task(ctrl, bo, bc, "open"))
    asyncio.create_task(main.button_task(ctrl, bc, bo, "close"))
    await clock.advance(100)
    bo.value(0)
    bc.value(0)
    # 3 s counts from debounced both-low (~50 ms after press), so hold 3.2 s
    await clock.advance(hold)
    assert main.ap_request is expect_ap
    bo.value(1)
    bc.value(1)
    await clock.advance(200)
    assert calls == []


async def test_ap_other_pulse_near_start_does_not_count(clock):  # adversary #1
    ctrl = fresh_ctrl()
    calls = record_manual(ctrl)
    bo, bc = MockPin(1), MockPin(1)
    asyncio.create_task(main.button_task(ctrl, bo, bc, "open"))
    asyncio.create_task(main.button_task(ctrl, bc, bo, "close"))
    await clock.advance(100)
    bo.value(0)  # OPEN held 3.1 s (t=100..3200)
    await clock.advance(60)
    bc.value(0)  # CLOSE 60 ms pulse near start
    await clock.advance(60)
    bc.value(1)
    await clock.advance(2980)  # t=3200, OPEN still held
    assert main.ap_request is False  # both were never low together for 3 s
    bo.value(1)
    await clock.advance(200)
    assert calls == []


# ---------------------------------------------------------------- led_task


@pytest.mark.parametrize(
    "st, red, green",
    [(GateState.IDLE_OPEN, 0, 1), (GateState.IDLE_CLOSED, 1, 0)],
)
async def test_led_steady_states(clock, st, red, green):  # #11
    ctrl = fresh_ctrl(st)
    r, g, y = RecLed(), RecLed(), RecLed()
    asyncio.create_task(main.led_task(ctrl, r, g, y))
    await clock.advance(1000)
    assert set(r.writes) == {red}
    assert set(g.writes) == {green}


async def test_led_init_all_off(clock):  # #11 (INIT part, R23)
    ctrl = fresh_ctrl(GateState.INIT)
    r, g, y = RecLed(), RecLed(), RecLed()
    asyncio.create_task(main.led_task(ctrl, r, g, y))
    await clock.advance(1000)
    assert set(r.writes) == {0}
    assert set(g.writes) == {0}
    assert set(y.writes) == {0}


@pytest.mark.parametrize(
    "st, which, period_ms, writes_in_1s",
    [
        (GateState.MOVING_OPEN, "green", 500, 3),
        (GateState.SAFETY_STOP, "red", 500, 3),
        (GateState.ERROR, "red", 125, 9),
    ],
)
async def test_led_blink_rates(clock, st, which, period_ms, writes_in_1s):  # #12
    ctrl = fresh_ctrl(st)
    r, g, y = RecLed(), RecLed(), RecLed()
    asyncio.create_task(main.led_task(ctrl, r, g, y))
    await clock.advance(1000)
    seq = (g if which == "green" else r).writes
    assert len(seq) == writes_in_1s
    assert all(seq[i] != seq[i + 1] for i in range(len(seq) - 1))  # strict toggle every write


@pytest.mark.parametrize("kind", ["rtc_false", "osf"])
async def test_led_yellow_overlay_unsynced_or_osf(clock, kind):  # #13
    if kind == "osf":
        ctrl = fresh_ctrl(GateState.IDLE_OPEN, rtc=MockRTC(osf=True))
        ctrl.tick()  # reads OSF -> rtc_synced False
    else:
        ctrl = fresh_ctrl(GateState.IDLE_OPEN)
        ctrl.rtc_synced = False
    r, g, y = RecLed(), RecLed(), RecLed()
    asyncio.create_task(main.led_task(ctrl, r, g, y))
    await clock.advance(1000)
    assert set(g.writes) == {1}  # green still solid
    assert set(r.writes) == {0}
    assert len(y.writes) == 3 and all(y.writes[i] != y.writes[i + 1] for i in range(2))  # 1 Hz


async def test_led_pcf_oserror_keeps_loop_alive(clock):  # #14
    ctrl = fresh_ctrl(GateState.IDLE_CLOSED)
    r, g, y = RecLed(), RecLed(fail=True), RecLed()
    t = asyncio.create_task(main.led_task(ctrl, r, g, y))
    await clock.advance(1000)
    assert r.writes and set(r.writes) == {1}
    assert not t.done()


# ---------------------------------------------------------------- hooks / main binding


async def test_error_event_fires_once_and_main_flushes(monkeypatch):  # #33 (main flush part)
    ctrl = fresh_ctrl(GateState.IDLE_CLOSED)
    ctrl._last_local = (2026, 6, 15, 720)
    flushes = []
    records = []
    monkeypatch.setattr(main.logs, "flush", lambda *a, **k: flushes.append(1) or True)
    monkeypatch.setattr(main.logs, "record", lambda *a: records.append(a))
    ctrl.on_event = main.make_on_event_log(ctrl)
    ctrl._halt(GateState.ERROR)
    ctrl._halt(GateState.ERROR)  # ERROR-once guard lives in state.py
    assert flushes == [1]
    assert records == [(logs.days_since_2025(2026, 6, 15), 720, logs.KIND_ERROR, 0)]


# ---------------------------------------------------------------- AP session


@pytest.mark.parametrize("mode", ["start_error", "cancel"])
async def test_ap_teardown_on_exception_and_cancel(clock, monkeypatch, mode):  # #34
    monkeypatch.setattr(main, "ap_request", True)
    ctrl = fresh_ctrl(GateState.IDLE_CLOSED)
    wifi, machine_mod, apps = FakeWifi(), FakeMachine(), []
    t = asyncio.create_task(
        main.ap_session(
            ctrl, make_factory(apps, fail=(mode == "start_error")), wifi, machine_mod, {"wifi": {"ap_password": ""}}
        )
    )
    await clock.advance(1000)
    if mode == "cancel":
        assert main.ap_active is True
        t.cancel()
        await clock.advance(10)
        assert t.cancelled()
    else:
        assert t.done() and isinstance(t.exception(), RuntimeError)
    assert apps[0].shutdowns == 1
    assert wifi.off_calls == 1
    assert not [c for c in machine_mod.calls if c[0] == "freq"]  # ap_session never touches CPU clock
    assert main.ap_active is False


async def test_ap_server_oserror_propagates_with_task_lacking_cancelled_result(clock, monkeypatch):  # adversary #2
    monkeypatch.setattr(main, "ap_request", True)
    ctrl = fresh_ctrl(GateState.IDLE_CLOSED)
    wifi, machine_mod, apps = FakeWifi(), FakeMachine(), []
    real_create = asyncio.create_task

    class CrashApp:
        def __init__(self):
            self.shutdowns = 0

        async def start_server(self, port):
            raise OSError("bind")

        def shutdown(self):
            self.shutdowns += 1

    def factory(_ctrl, _touch):
        apps.append(CrashApp())
        return apps[-1]

    class TaskNoResult:  # MicroPython Task surface: done() / cancel() only
        def __init__(self, coro):
            self._t = real_create(coro)

        def done(self):
            return self._t.done()

        def cancel(self):
            return self._t.cancel()

    t = real_create(main.ap_session(ctrl, factory, wifi, machine_mod, {"wifi": {"ap_password": ""}}))
    monkeypatch.setattr(main.asyncio, "create_task", TaskNoResult)
    await clock.advance(1000)
    assert t.done() and isinstance(t.exception(), OSError)  # propagates -> supervised path
    assert apps[0].shutdowns == 1
    assert wifi.off_calls == 1
    assert not [c for c in machine_mod.calls if c[0] == "freq"]  # ap_session never touches CPU clock
    assert main.ap_active is False


async def test_ap_idle_timeout_shuts_down_after_10_min(clock, monkeypatch):  # #36
    monkeypatch.setattr(main, "ap_request", True)
    ctrl = fresh_ctrl(GateState.IDLE_CLOSED)
    apps = []
    asyncio.create_task(
        main.ap_session(ctrl, make_factory(apps), FakeWifi(), FakeMachine(), {"wifi": {"ap_password": ""}})
    )
    await clock.advance(1000)
    await clock.advance(10 * 60_000 + 60_000)
    assert apps[0].shutdowns == 1
    assert main.ap_active is False


@pytest.mark.parametrize("how", ["cancel", "idle"])
async def test_ap_starts_dns_task_and_cancels_on_teardown(clock, monkeypatch, fake_dns, how):
    monkeypatch.setattr(main, "ap_request", True)
    ctrl = fresh_ctrl(GateState.IDLE_CLOSED)
    apps = []
    t = asyncio.create_task(
        main.ap_session(ctrl, make_factory(apps), FakeWifi(), FakeMachine(), {"wifi": {"ap_password": ""}})
    )
    await clock.advance(1000)
    assert fake_dns.starts == 1
    assert fake_dns.cancelled == 0
    if how == "cancel":
        t.cancel()
        await clock.advance(10)
    else:
        await clock.advance(10 * 60_000 + 60_000)
    assert fake_dns.cancelled == 1
    assert main.ap_active is False


async def test_ap_http_touch_resets_idle_timeout(clock, monkeypatch):  # #36 (touch part)
    monkeypatch.setattr(main, "ap_request", True)
    ctrl = fresh_ctrl(GateState.IDLE_CLOSED)
    apps = []
    asyncio.create_task(
        main.ap_session(ctrl, make_factory(apps), FakeWifi(), FakeMachine(), {"wifi": {"ap_password": ""}})
    )
    await clock.advance(1000)
    for _ in range(6):  # 30 min, a request every 5 min
        await clock.advance(300_000)
        apps[0].touch()
    assert apps[0].shutdowns == 0
    assert main.ap_active is True


async def test_ap_deferred_while_moving_then_starts(clock):  # #37
    ctrl = fresh_ctrl(GateState.MOVING_CLOSE)
    wifi = FakeWifi()
    main.ap_request = True
    asyncio.create_task(main.ap_session(ctrl, make_factory([]), wifi, FakeMachine(), {"wifi": {"ap_password": ""}}))
    await clock.advance(1000)
    assert wifi.on_calls == []
    assert main.ap_request is True
    ctrl.state = GateState.IDLE_OPEN
    await clock.advance(500)
    assert len(wifi.on_calls) == 1


async def test_ap_allowed_in_safety_stop_does_not_clear(clock):  # #38
    ctrl = fresh_ctrl(GateState.SAFETY_STOP)
    wifi = FakeWifi()
    main.ap_request = True
    t = asyncio.create_task(main.ap_session(ctrl, make_factory([]), wifi, FakeMachine(), {"wifi": {"ap_password": ""}}))
    await clock.advance(1000)
    assert len(wifi.on_calls) == 1
    assert ctrl.state == GateState.SAFETY_STOP
    t.cancel()


@pytest.mark.parametrize("pw", ["", "abcdefgh"])
async def test_ap_on_receives_config_password_incl_empty(clock, pw):  # #39
    ctrl = fresh_ctrl(GateState.IDLE_CLOSED)
    wifi = FakeWifi()
    main.ap_request = True
    t = asyncio.create_task(main.ap_session(ctrl, make_factory([]), wifi, FakeMachine(), {"wifi": {"ap_password": pw}}))
    await clock.advance(1000)
    assert wifi.on_calls == [("Szczesliwy_Kurczak", pw)]
    t.cancel()


@pytest.mark.parametrize(
    "pw, ok",
    [("", True), ("a" * 8, True), ("a" * 63, True), ("a" * 7, False), ("a" * 64, False), (12345678, False)],
)
def test_ap_password_validation(pw, ok):  # #40
    cfg = default_config()
    cfg["wifi"] = {"ap_password": pw, "webrepl_password": ""}
    if ok:
        validate_config(cfg)
    else:
        with pytest.raises(ConfigError, match="ap_password"):
            validate_config(cfg)


# ---------------------------------------------------------------- WebREPL (active only while AP runs)


@pytest.mark.parametrize("how", ["cancel", "idle"])
async def test_webrepl_started_with_password_and_stopped_on_teardown(clock, monkeypatch, fake_webrepl, how):
    monkeypatch.setattr(main, "ap_request", True)
    ctrl = fresh_ctrl(GateState.IDLE_CLOSED)
    cfg = {"wifi": {"ap_password": "", "webrepl_password": "secret9"}}
    t = asyncio.create_task(main.ap_session(ctrl, make_factory([]), FakeWifi(), FakeMachine(), cfg))
    await clock.advance(1000)
    assert fake_webrepl.starts == ["secret9"]
    assert fake_webrepl.stops == 0
    if how == "cancel":
        t.cancel()
        await clock.advance(10)
    else:
        await clock.advance(10 * 60_000 + 60_000)
    assert fake_webrepl.stops == 1
    assert main.ap_active is False


async def test_webrepl_not_started_when_password_empty(clock, monkeypatch, fake_webrepl):
    monkeypatch.setattr(main, "ap_request", True)
    ctrl = fresh_ctrl(GateState.IDLE_CLOSED)
    cfg = {"wifi": {"ap_password": "", "webrepl_password": ""}}
    t = asyncio.create_task(main.ap_session(ctrl, make_factory([]), FakeWifi(), FakeMachine(), cfg))
    await clock.advance(1000)
    t.cancel()
    await clock.advance(10)
    assert fake_webrepl.starts == []
    assert fake_webrepl.stops == 0


async def test_webrepl_start_error_keeps_ap_up_and_stop_still_runs(clock, monkeypatch, fake_webrepl):
    fake_webrepl.fail = True
    monkeypatch.setattr(main, "ap_request", True)
    ctrl = fresh_ctrl(GateState.IDLE_CLOSED)
    wifi = FakeWifi()
    cfg = {"wifi": {"ap_password": "", "webrepl_password": "secret9"}}
    t = asyncio.create_task(main.ap_session(ctrl, make_factory([]), wifi, FakeMachine(), cfg))
    await clock.advance(1000)
    assert main.ap_active is True and len(wifi.on_calls) == 1 and not t.done()
    t.cancel()
    await clock.advance(10)
    assert fake_webrepl.stops == 1
    assert wifi.off_calls == 1


# ---------------------------------------------------------------- boot / crash policy


def test_boot_not_ok_enters_error_without_halt_from_main():  # #41
    ctrl = fresh_ctrl(GateState.IDLE_CLOSED)
    calls = []
    real_enter = ctrl.enter_error

    def spy():
        calls.append("enter_error")
        real_enter()

    ctrl.enter_error = spy
    main.boot_checks(ctrl, False)
    assert calls == ["enter_error"]
    assert ctrl.state == GateState.ERROR
    assert main.ap_request is True
    assert main.ap_auto_done is True


async def test_task_crash_resets_via_supervised(monkeypatch, capsys):  # #42
    fm = FakeMachine()
    flushes = []
    monkeypatch.setattr(main, "machine", fm)
    monkeypatch.setattr(main.logs, "flush", lambda *a, **k: flushes.append(1))

    async def boom():
        raise RuntimeError("probe")

    await main.supervised("probe", boom())
    assert fm.calls == [("reset",)]
    assert flushes == [1]
    assert "crashed" in capsys.readouterr().out
