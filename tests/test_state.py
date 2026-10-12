# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
"""State machine suite for CoopController (per SPEC.md).

RTC tuple hour at index 3. January: local == CET. June: local = CET + 60.
Limit pins and nFAULT active-low (0 = active). Schedule fixed (360, 1080) by conftest.
Phase 4 items are tagged "# 04 #N" (docs/phases/04-async-tasks.md numbering).
"""

import asyncio
import json
import time

import pytest

import state as state_mod
from state import GateState, MovementTrigger

UNSYNCED_YEAR = 2000


def rtc_at(ctrl, y, mo, d, h, mi):
    ctrl.rtc.set_datetime((y, mo, d, h, mi, 0, 0))


def set_limit(pin, reached):
    pin.value(0 if reached else 1)


def init_tick(ctrl):
    """INIT decides only on the 2nd consistent limit read (R37)."""
    ctrl.tick()
    ctrl.tick()


async def wait_for(pred, timeout=1.0):
    end = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > end:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)


def _raise_oserror(*_):
    raise OSError("I2C fail")


@pytest.fixture
def clock(monkeypatch):
    now = [0]
    monkeypatch.setattr(state_mod, "ticks_ms", lambda: now[0])
    return now


@pytest.fixture
def sched_calls(monkeypatch):
    calls: list[tuple[int, int, int]] = []

    def counting(cfg, y, m, d):
        calls.append((y, m, d))
        return (360, 1080)

    monkeypatch.setattr(state_mod, "schedule", counting)
    return calls


@pytest.fixture
def vsleep(monkeypatch):
    """Virtual sleep: no real delay, returns list of requested ms. Virtual time = sum(list).
    Does not touch ticks_ms, so move timeouts never fire by accident."""
    slept: list[int] = []

    async def fake(ms):
        slept.append(ms)
        await asyncio.sleep(0)

    monkeypatch.setattr(state_mod, "sleep_ms", fake)
    return slept


def record(ctrl, vsleep):
    """Tap nsleep / motor writes: list of (name, virtual_time_ms) in call order."""
    events: list[tuple[str, int]] = []
    real_nsleep = ctrl.nsleep.value

    def nsleep(v=None):
        if v is not None:
            events.append((f"nsleep{v}", sum(vsleep)))
        return real_nsleep(v)

    ctrl.nsleep.value = nsleep
    for name in ("stop", "forward", "backward"):
        setattr(ctrl.motor, name, _tap(events, name, getattr(ctrl.motor, name), vsleep))
    return events


def _tap(events, name, real, vsleep):
    def f():
        events.append((name, sum(vsleep)))
        return real()

    return f


# ---------------------------------------------------------------------------
# INIT
# ---------------------------------------------------------------------------


def test_init_top_limit_goes_idle_open(ctrl):
    ctrl.state = GateState.INIT
    set_limit(ctrl.limit_top, True)
    init_tick(ctrl)
    assert ctrl.state == GateState.IDLE_OPEN


def test_init_bottom_limit_goes_idle_closed(ctrl):
    ctrl.state = GateState.INIT
    set_limit(ctrl.limit_bottom, True)
    ctrl.rtc.set_datetime((2026, 6, 15, 21, 0, 0, 0))  # night: closed is already desired
    init_tick(ctrl)
    assert ctrl.state == GateState.IDLE_CLOSED


def test_init_bottom_limit_by_day_opens_same_tick(ctrl):
    ctrl.state = GateState.INIT
    set_limit(ctrl.limit_bottom, True)
    init_tick(ctrl)  # 12:00 CET, desired open
    assert ctrl.state == GateState.MOVING_OPEN


def test_init_both_limits_is_error(ctrl):
    ctrl.state = GateState.INIT
    set_limit(ctrl.limit_top, True)
    set_limit(ctrl.limit_bottom, True)
    init_tick(ctrl)
    assert ctrl.state == GateState.ERROR


def test_init_no_limits_synced_daytime_moves_open(ctrl):
    ctrl.state = GateState.INIT
    rtc_at(ctrl, 2026, 1, 15, 12, 0)
    init_tick(ctrl)
    assert ctrl.state == GateState.MOVING_OPEN
    assert ctrl._trigger == MovementTrigger.AUTO


def test_init_no_limits_synced_night_moves_close(ctrl):
    ctrl.state = GateState.INIT
    rtc_at(ctrl, 2026, 1, 15, 20, 0)
    init_tick(ctrl)
    assert ctrl.state == GateState.MOVING_CLOSE


def test_init_no_limits_unsynced_moves_close(ctrl):
    ctrl.state = GateState.INIT
    rtc_at(ctrl, UNSYNCED_YEAR, 1, 1, 10, 0)
    init_tick(ctrl)
    assert ctrl.state == GateState.MOVING_CLOSE


# 04 #25
@pytest.mark.parametrize(
    "case, expected",
    [
        ("top", GateState.IDLE_OPEN),
        ("bottom", GateState.MOVING_OPEN),  # bottom-only at 12:00 -> IDLE_CLOSED, then auto open
        ("both", GateState.ERROR),
        ("neither_day", GateState.MOVING_OPEN),
        ("neither_night", GateState.MOVING_CLOSE),
        ("neither_unsynced", GateState.MOVING_CLOSE),
    ],
)
def test_init_two_consistent_reads_all_outcomes(ctrl, case, expected):
    ctrl.state = GateState.INIT
    set_limit(ctrl.limit_top, case in ("top", "both"))
    set_limit(ctrl.limit_bottom, case in ("bottom", "both"))
    if case == "neither_night":
        rtc_at(ctrl, 2026, 1, 15, 20, 0)
    elif case == "neither_unsynced":
        rtc_at(ctrl, UNSYNCED_YEAR, 1, 1, 10, 0)
    else:
        rtc_at(ctrl, 2026, 1, 15, 12, 0)
    ctrl.tick()
    assert ctrl.state == GateState.INIT  # 1 read: no decision
    ctrl.tick()
    assert ctrl.state == expected


# 04 #26
def test_init_glitch_then_disagree_resets(ctrl):
    ctrl.state = GateState.INIT
    set_limit(ctrl.limit_top, True)
    set_limit(ctrl.limit_bottom, False)
    ctrl.tick()  # (top, not bottom)
    set_limit(ctrl.limit_top, False)
    set_limit(ctrl.limit_bottom, True)
    ctrl.tick()  # (bottom, not top): disagrees -> restart count
    assert ctrl.state == GateState.INIT
    assert ctrl.motor.commands == []
    ctrl.tick()  # 2nd consistent read of (bottom only) -> decide
    assert ctrl.state == GateState.MOVING_OPEN  # bottom-only -> IDLE_CLOSED, 12:00 desired open


# ---------------------------------------------------------------------------
# Schedule-driven auto open / close
# ---------------------------------------------------------------------------


def test_auto_open_at_0600_local(ctrl):
    ctrl.state = GateState.IDLE_CLOSED
    rtc_at(ctrl, 2026, 1, 15, 6, 0)
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_OPEN
    assert ctrl._trigger == MovementTrigger.AUTO


def test_no_auto_open_at_0559(ctrl):
    ctrl.state = GateState.IDLE_CLOSED
    rtc_at(ctrl, 2026, 1, 15, 5, 59)
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_CLOSED


def test_auto_close_at_1800_local(ctrl):
    ctrl.state = GateState.IDLE_OPEN
    rtc_at(ctrl, 2026, 1, 15, 18, 0)
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_CLOSE
    assert ctrl._trigger == MovementTrigger.AUTO


def test_no_auto_close_at_1759(ctrl):
    ctrl.state = GateState.IDLE_OPEN
    rtc_at(ctrl, 2026, 1, 15, 17, 59)
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_OPEN


def test_unsynced_rtc_no_auto_open(ctrl):
    ctrl.state = GateState.IDLE_CLOSED
    rtc_at(ctrl, UNSYNCED_YEAR, 1, 1, 10, 0)
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_CLOSED


def test_schedule_recomputed_once_per_local_date(ctrl, sched_calls):
    ctrl.state = GateState.IDLE_CLOSED
    rtc_at(ctrl, 2026, 1, 15, 22, 0)
    ctrl.tick()
    ctrl.tick()
    assert sched_calls == [(2026, 1, 15)]
    rtc_at(ctrl, 2026, 1, 15, 23, 30)
    ctrl.tick()
    assert sched_calls == [(2026, 1, 15)]
    rtc_at(ctrl, 2026, 1, 16, 0, 30)
    ctrl.tick()
    assert sched_calls == [(2026, 1, 15), (2026, 1, 16)]


def test_cest_2330_cet_is_next_local_day(ctrl, sched_calls):
    # June = CEST: 23:30 CET = 00:30 local on the 16th
    ctrl.state = GateState.IDLE_CLOSED
    rtc_at(ctrl, 2026, 6, 15, 23, 30)
    ctrl.tick()
    assert sched_calls[-1] == (2026, 6, 16)
    assert ctrl.state == GateState.IDLE_CLOSED


# ---------------------------------------------------------------------------
# Manual hold lifecycle (async: real move tasks, limit pins flipped)
# ---------------------------------------------------------------------------


async def test_manual_close_at_1000_holds_until_1800(ctrl):
    ctrl.state = GateState.IDLE_OPEN
    rtc_at(ctrl, 2026, 1, 15, 10, 0)
    ctrl.manual_move("close", "button")
    await wait_for(lambda: ctrl.nsleep.value() == 1)
    set_limit(ctrl.limit_bottom, True)
    await wait_for(lambda: ctrl.state == GateState.MANUAL_HOLD_CLOSED)
    assert ctrl.nsleep.value() == 0

    for h, mi in ((12, 0), (17, 59)):
        rtc_at(ctrl, 2026, 1, 15, h, mi)
        ctrl.tick()
        assert ctrl.state == GateState.MANUAL_HOLD_CLOSED

    rtc_at(ctrl, 2026, 1, 15, 18, 0)
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_CLOSED

    rtc_at(ctrl, 2026, 1, 16, 5, 59)
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_CLOSED

    rtc_at(ctrl, 2026, 1, 16, 6, 0)
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_OPEN
    set_limit(ctrl.limit_top, True)
    await wait_for(lambda: ctrl.state == GateState.IDLE_OPEN)


async def test_manual_open_at_2200_holds_until_0600_then_closes_1800(ctrl):
    ctrl.state = GateState.IDLE_CLOSED
    rtc_at(ctrl, 2026, 1, 15, 22, 0)
    ctrl.manual_move("open", "button")
    await wait_for(lambda: ctrl.nsleep.value() == 1)
    set_limit(ctrl.limit_top, True)
    await wait_for(lambda: ctrl.state == GateState.MANUAL_HOLD_OPEN)

    rtc_at(ctrl, 2026, 1, 15, 23, 59)
    ctrl.tick()
    assert ctrl.state == GateState.MANUAL_HOLD_OPEN

    rtc_at(ctrl, 2026, 1, 16, 5, 59)
    ctrl.tick()
    assert ctrl.state == GateState.MANUAL_HOLD_OPEN

    rtc_at(ctrl, 2026, 1, 16, 6, 0)
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_OPEN

    rtc_at(ctrl, 2026, 1, 16, 17, 59)
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_OPEN

    rtc_at(ctrl, 2026, 1, 16, 18, 0)
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_CLOSE
    set_limit(ctrl.limit_bottom, True)
    await wait_for(lambda: ctrl.state == GateState.IDLE_CLOSED)


# ---------------------------------------------------------------------------
# Manual move rules (sync)
# ---------------------------------------------------------------------------


def test_manual_ignored_in_error(ctrl):
    ctrl.state = GateState.ERROR
    ctrl.manual_move("open", "button")
    assert ctrl.state == GateState.ERROR
    assert ctrl.motor.commands == []


# 04 #16
@pytest.mark.parametrize("source", ["button", "web"])
def test_manual_error_refused_both_sources(ctrl, source):
    ctrl.state = GateState.ERROR
    assert ctrl.manual_move("open", source) is False
    assert ctrl.state == GateState.ERROR
    assert ctrl.motor.commands == []


# 04 #15
def test_manual_web_refused_in_safety_stop_button_recovers(ctrl):
    ctrl.state = GateState.SAFETY_STOP
    rtc_at(ctrl, 2026, 1, 15, 12, 0)
    assert ctrl.manual_move("open", "web") is False
    assert ctrl.state == GateState.SAFETY_STOP
    assert ctrl.motor.commands == []
    assert ctrl.manual_move("open", "button") is True
    assert ctrl.state == GateState.MOVING_OPEN
    assert ctrl._trigger == MovementTrigger.RECOVERY


# 04 #17
def test_manual_same_direction_noop_init_allowed_source_required(ctrl):
    ctrl.state = GateState.IDLE_OPEN
    rtc_at(ctrl, 2026, 1, 15, 12, 0)
    gen = ctrl._move_gen
    assert ctrl.manual_move("open", "button") is False
    assert ctrl._move_gen == gen  # no _start_move
    assert ctrl.state == GateState.IDLE_OPEN

    ctrl.state = GateState.INIT
    assert ctrl.manual_move("open", "button") is True
    assert ctrl.state == GateState.MOVING_OPEN
    assert ctrl._trigger == MovementTrigger.MANUAL

    with pytest.raises(TypeError):
        ctrl.manual_move("open")


def test_same_direction_manual_is_noop(ctrl):
    ctrl.state = GateState.IDLE_CLOSED
    rtc_at(ctrl, 2026, 1, 15, 6, 0)
    ctrl.tick()  # AUTO open starts
    assert ctrl.state == GateState.MOVING_OPEN
    assert ctrl.manual_move("open", "button") is False
    assert ctrl.state == GateState.MOVING_OPEN
    assert ctrl._trigger == MovementTrigger.AUTO
    assert ctrl.motor.commands == []


@pytest.mark.parametrize("terminal", [GateState.SAFETY_STOP, GateState.ERROR])
def test_tick_no_op_in_terminal_states(ctrl, terminal):
    ctrl.state = terminal
    rtc_at(ctrl, 2026, 1, 15, 6, 0)
    ctrl.tick()
    assert ctrl.state == terminal
    assert ctrl.motor.commands == []


# 04 #6
@pytest.mark.parametrize("terminal", [GateState.SAFETY_STOP, GateState.ERROR])
def test_tick_reads_clock_in_safety_stop_and_error(ctrl, terminal):
    fired = []
    ctrl.on_day_change = lambda prev, new: fired.append((prev, new))
    ctrl.state = terminal
    rtc_at(ctrl, 2026, 1, 15, 6, 0)  # desired open, but terminal: must not move
    ctrl.tick()
    assert ctrl.rtc_synced is True
    assert ctrl._last_local == (2026, 1, 15, 360)
    assert ctrl.state == terminal
    assert ctrl.motor.commands == []
    rtc_at(ctrl, 2026, 1, 16, 0, 30)
    ctrl.tick()
    assert fired == [((2026, 1, 15), (2026, 1, 16))]
    assert ctrl._last_local == (2026, 1, 16, 30)
    assert ctrl.state == terminal
    assert ctrl.motor.commands == []


def test_tick_timeout_backstop_without_task(ctrl, clock):
    ctrl.state = GateState.IDLE_CLOSED
    rtc_at(ctrl, 2026, 1, 15, 12, 0)
    ctrl.manual_move("open", "button")  # sync: no loop, no task
    clock[0] = 22_000  # > move_timeout_s 21
    ctrl.tick()
    assert ctrl.state == GateState.SAFETY_STOP
    assert ctrl.motor.commands[-1] == "stop"


# ---------------------------------------------------------------------------
# I2C error handling
# ---------------------------------------------------------------------------


def test_i2c_three_failures_is_error(ctrl):
    for _ in range(3):
        ctrl._i2c_call(_raise_oserror)
    assert ctrl.state == GateState.ERROR


def test_i2c_success_resets_failure_count(ctrl):
    ctrl._i2c_call(_raise_oserror)
    ctrl._i2c_call(_raise_oserror)
    ctrl._i2c_call(lambda: (2026, 1, 15, 12, 0, 0, 0))
    ctrl._i2c_call(_raise_oserror)
    ctrl._i2c_call(_raise_oserror)
    assert ctrl.state != GateState.ERROR


# 04 #23
def test_halt_error_after_repeated_i2c_fail(ctrl):
    events = []
    ctrl.on_event = lambda kind, trig: events.append((kind, trig))
    ctrl.state = GateState.IDLE_CLOSED
    ctrl.rtc.datetime = _raise_oserror
    for _ in range(3):
        ctrl.tick()
    assert ctrl.state == GateState.ERROR
    ctrl.tick()  # ERROR path still reads clock: fails again, must stay silent
    assert events == [("error", MovementTrigger.AUTO)]


# ---------------------------------------------------------------------------
# Safety stop (async)
# ---------------------------------------------------------------------------


async def test_nfault_during_move_is_safety_stop(ctrl):
    ctrl.state = GateState.IDLE_CLOSED
    rtc_at(ctrl, 2026, 1, 15, 6, 0)
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_OPEN
    await wait_for(lambda: ctrl.nsleep.value() == 1)
    ctrl.nfault.value(0)
    await wait_for(lambda: ctrl.state == GateState.SAFETY_STOP)
    assert ctrl.nsleep.value() == 0
    assert ctrl.motor.commands[-1] == "stop"


async def test_timeout_in_run_move_is_safety_stop_without_tick(ctrl, clock):
    ctrl.state = GateState.IDLE_CLOSED
    rtc_at(ctrl, 2026, 1, 15, 12, 0)
    ctrl.manual_move("open", "button")
    await wait_for(lambda: ctrl.nsleep.value() == 1)
    clock[0] = 22_000  # > move_timeout_s 21, no tick() called
    await wait_for(lambda: ctrl.state == GateState.SAFETY_STOP)
    assert ctrl.nsleep.value() == 0
    assert ctrl.motor.commands[-1] == "stop"


async def test_i2c_error_during_move_stays_error_after_timeout(ctrl, clock, vsleep):
    rtc_at(ctrl, 2026, 1, 15, 12, 0)
    ctrl.manual_move("open", "button")
    await wait_for(lambda: ctrl.nsleep.value() == 1)
    ctrl.rtc.datetime = _raise_oserror
    for _ in range(3):  # clock reads during an I2C fault burst (status_json has no RTC I/O, R32)
        ctrl._read_clock()
    assert ctrl.state == GateState.ERROR
    clock[0] = 22_000  # past move timeout
    await asyncio.sleep(0.05)
    assert ctrl.state == GateState.ERROR


def test_halt_with_dead_i2c_still_sets_state_and_sleeps_driver(ctrl):
    ctrl.state = GateState.MOVING_OPEN
    ctrl.nsleep.value(1)
    ctrl.motor.stop = _raise_oserror
    ctrl._halt(GateState.SAFETY_STOP)
    assert ctrl.state == GateState.SAFETY_STOP
    assert ctrl.nsleep.value() == 0


async def test_motor_drive_i2c_failure_is_error_with_driver_asleep(ctrl):
    rtc_at(ctrl, 2026, 1, 15, 12, 0)
    ctrl.motor.forward = _raise_oserror
    ctrl.manual_move("open", "button")
    await wait_for(lambda: ctrl.state == GateState.ERROR)
    assert ctrl.nsleep.value() == 0


async def test_limit_stop_with_dead_i2c_sleeps_driver_and_reaches_idle(ctrl):
    ctrl.manual_move("open", "button")
    await wait_for(lambda: ctrl.nsleep.value() == 1)
    ctrl.motor.stop = _raise_oserror
    set_limit(ctrl.limit_top, True)
    await wait_for(lambda: ctrl.state not in (GateState.MOVING_OPEN, GateState.MOVING_CLOSE))
    assert ctrl.nsleep.value() == 0
    assert ctrl.state == GateState.MANUAL_HOLD_OPEN


async def test_safety_stop_recovery_goes_idle_not_hold(ctrl):
    ctrl.state = GateState.SAFETY_STOP
    ctrl.manual_move("close", "button")
    assert ctrl._trigger == MovementTrigger.RECOVERY
    await wait_for(lambda: ctrl.nsleep.value() == 1)
    set_limit(ctrl.limit_bottom, True)
    await wait_for(lambda: ctrl.state == GateState.IDLE_CLOSED)
    assert ctrl.nsleep.value() == 0


# 04 #18
async def test_reversal_dead_time_and_stop_order(ctrl, vsleep):
    ctrl.state = GateState.IDLE_CLOSED
    rtc_at(ctrl, 2026, 1, 15, 12, 0)
    ctrl.manual_move("open", "button")
    await wait_for(lambda: ctrl.motor.state == "forward")
    events = record(ctrl, vsleep)
    t0 = sum(vsleep)
    assert ctrl.manual_move("close", "button") is True
    assert events == []  # no motor.stop at _start_move entry
    await wait_for(lambda: any(n == "backward" for n, _ in events))
    assert [n for n, _ in events[:4]] == ["nsleep0", "stop", "nsleep1", "backward"]
    assert events[0][1] == t0  # nSLEEP LOW at t=0
    assert events[1][1] == t0  # IN1=IN2 LOW at t=0
    assert events[2][1] - t0 >= 200  # dead time before driving opposite
    assert events[3][1] - t0 >= 200
    set_limit(ctrl.limit_bottom, True)
    await wait_for(lambda: ctrl.state == GateState.MANUAL_HOLD_CLOSED)


# 04 #19
async def test_reversal_supersedes_old_run_move(ctrl, vsleep):
    ctrl.state = GateState.IDLE_CLOSED
    rtc_at(ctrl, 2026, 1, 15, 12, 0)
    ctrl.manual_move("open", "button")
    await wait_for(lambda: ctrl.motor.state == "forward")
    n = len(ctrl.motor.commands)
    assert ctrl.manual_move("close", "button") is True
    await wait_for(lambda: "backward" in ctrl.motor.commands[n:])
    await asyncio.sleep(0.02)
    assert "forward" not in ctrl.motor.commands[n:]
    assert ctrl.state == GateState.MOVING_CLOSE
    set_limit(ctrl.limit_bottom, True)
    await wait_for(lambda: ctrl.state == GateState.MANUAL_HOLD_CLOSED)
    set_limit(ctrl.limit_top, True)  # old open run must not finish and overwrite state
    await asyncio.sleep(0.02)
    assert ctrl.state == GateState.MANUAL_HOLD_CLOSED


# 04 #20
async def test_reversal_pcf_oserror_goes_error(ctrl, vsleep):
    ctrl.state = GateState.IDLE_CLOSED
    rtc_at(ctrl, 2026, 1, 15, 12, 0)
    ctrl.manual_move("open", "button")
    await wait_for(lambda: ctrl.nsleep.value() == 1)
    ctrl.motor.backward = _raise_oserror
    assert ctrl.manual_move("close", "button") is True
    await wait_for(lambda: ctrl.state == GateState.ERROR)
    assert ctrl.nsleep.value() == 0


# 04 #21
@pytest.mark.parametrize("path", ["halt", "limit", "reversal"])
async def test_stop_order_nsleep_first_everywhere(ctrl, vsleep, path):
    ctrl.state = GateState.IDLE_CLOSED
    rtc_at(ctrl, 2026, 1, 15, 12, 0)
    ctrl.manual_move("open", "button")
    await wait_for(lambda: ctrl.nsleep.value() == 1)
    events = record(ctrl, vsleep)
    if path == "halt":
        ctrl._halt(GateState.SAFETY_STOP)
    elif path == "limit":
        set_limit(ctrl.limit_top, True)
        await wait_for(lambda: ctrl.state == GateState.MANUAL_HOLD_OPEN)
    else:
        ctrl.manual_move("close", "button")
        await wait_for(lambda: any(n == "backward" for n, _ in events))
    names = [n for n, _ in events]
    assert "stop" in names
    assert names.index("nsleep0") < names.index("stop")
    if path == "reversal":
        set_limit(ctrl.limit_bottom, True)
        await wait_for(lambda: ctrl.state == GateState.MANUAL_HOLD_CLOSED)


# 04 #22
async def test_halt_kills_running_run_move(ctrl, vsleep):
    ctrl.state = GateState.IDLE_CLOSED
    rtc_at(ctrl, 2026, 1, 15, 12, 0)
    ctrl.manual_move("open", "button")
    await wait_for(lambda: ctrl.nsleep.value() == 1)
    n = len(ctrl.motor.commands)
    ctrl._halt(GateState.SAFETY_STOP)
    set_limit(ctrl.limit_top, True)
    await asyncio.sleep(0.02)
    assert ctrl.state == GateState.SAFETY_STOP
    assert "forward" not in ctrl.motor.commands[n:]
    assert ctrl.nsleep.value() == 0


async def test_opposite_manual_stops_then_reverses(ctrl, vsleep):
    ctrl.state = GateState.IDLE_CLOSED
    rtc_at(ctrl, 2026, 1, 15, 12, 0)
    ctrl.manual_move("open", "button")
    assert ctrl.state == GateState.MOVING_OPEN
    await wait_for(lambda: ctrl.motor.state == "forward")
    assert ctrl.manual_move("close", "button") is True
    assert ctrl.state == GateState.MOVING_CLOSE
    assert ctrl._trigger == MovementTrigger.MANUAL
    await wait_for(lambda: "backward" in ctrl.motor.commands)
    cmds = ctrl.motor.commands
    assert cmds[cmds.index("backward") - 1] == "stop"
    set_limit(ctrl.limit_bottom, True)
    await wait_for(lambda: ctrl.state == GateState.MANUAL_HOLD_CLOSED)


# ---------------------------------------------------------------------------
# OSF / unsynced
# ---------------------------------------------------------------------------


# 04 #27
def test_osf_treated_as_unsynced(ctrl):
    ctrl.state = GateState.IDLE_CLOSED
    rtc_at(ctrl, 2026, 1, 15, 9, 0)  # 09:00: open is desired if synced
    ctrl.rtc.osf_flag = True
    ctrl.tick()
    assert ctrl.today_schedule is None
    assert ctrl.desired(540) is None
    assert ctrl.rtc_synced is False
    assert ctrl.state == GateState.IDLE_CLOSED


# 04 #28
def test_osf_clear_restores_auto_move(ctrl):
    ctrl.state = GateState.IDLE_CLOSED
    rtc_at(ctrl, 2026, 1, 15, 9, 0)
    ctrl.rtc.osf_flag = True
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_CLOSED
    rtc_at(ctrl, 2026, 1, 15, 9, 0)  # set_datetime clears OSF (R17)
    assert ctrl.rtc.osf() is False
    ctrl.tick()
    assert ctrl.today_schedule == (360, 1080)
    assert ctrl.state == GateState.MOVING_OPEN


# 04 #29
async def test_hold_expires_on_first_valid_desired_when_pressed_unsynced(ctrl, vsleep):
    ctrl.state = GateState.IDLE_OPEN
    rtc_at(ctrl, UNSYNCED_YEAR, 1, 1, 10, 0)
    assert ctrl.manual_move("close", "button") is True
    assert ctrl._hold_desired is None
    await wait_for(lambda: ctrl.nsleep.value() == 1)
    set_limit(ctrl.limit_bottom, True)
    await wait_for(lambda: ctrl.state == GateState.MANUAL_HOLD_CLOSED)
    rtc_at(ctrl, 2026, 1, 15, 10, 0)  # first valid desired (open) -> hold expires (R38)
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_OPEN
    assert ctrl._trigger == MovementTrigger.AUTO
    set_limit(ctrl.limit_top, True)
    await wait_for(lambda: ctrl.state == GateState.IDLE_OPEN)


async def test_manual_close_with_one_rtc_glitch_keeps_hold(ctrl):
    """One RTC OSError during manual_move sets _hold_desired='?'; first good tick
    adopts current desired ('open') -> hold_desired=='open', hold does NOT expire
    because schedule hasn't moved on yet. Proves bug fix: else None caused
    immediate expiry (want='open' != None -> True)."""
    ctrl.state = GateState.IDLE_OPEN
    rtc_at(ctrl, 2026, 6, 15, 12, 0)  # 12:00 CET = minute 720, inside 360..1080 -> desired open

    real_datetime = ctrl.rtc.datetime
    fired = [0]

    def glitch_once():
        fired[0] += 1
        if fired[0] == 1:
            raise OSError("RTC glitch")
        return real_datetime()

    ctrl.rtc.datetime = glitch_once
    ctrl.manual_move("close", "button")
    assert ctrl._hold_desired == "?"  # glitch sentinel, not None

    ctrl.rtc.datetime = real_datetime  # restore before limit fires

    await wait_for(lambda: ctrl.nsleep.value() == 1)
    set_limit(ctrl.limit_bottom, True)
    await wait_for(lambda: ctrl.state == GateState.MANUAL_HOLD_CLOSED)

    for _ in range(3):
        rtc_at(ctrl, 2026, 6, 15, 12, 0)
        ctrl.tick()
        assert ctrl.state == GateState.MANUAL_HOLD_CLOSED  # hold must not expire
    assert ctrl.motor.state == "stop"  # no reversal triggered


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


# 04 #30
def test_day_change_hook_once_prev_from_last_local(ctrl):
    calls = []
    ctrl.on_day_change = lambda prev, new: calls.append((prev, new))
    ctrl.state = GateState.IDLE_CLOSED
    rtc_at(ctrl, 2024, 6, 1, 2, 0)  # night: closed desired, no move
    ctrl.tick()
    assert calls == []  # first sync: no hook
    ctrl._date = None  # invalidated by POST config/time (R30)
    rtc_at(ctrl, 2024, 6, 2, 0, 30)
    ctrl.tick()
    assert calls == [((2024, 6, 1), (2024, 6, 2))]  # prev from _last_local, not _date
    ctrl.tick()
    assert len(calls) == 1


# 04 #31
def test_hook_exceptions_never_kill_tick(ctrl):
    def boom(*_):
        raise RuntimeError("hook")

    ctrl.on_event = boom
    ctrl.on_day_change = boom
    ctrl.state = GateState.IDLE_CLOSED
    rtc_at(ctrl, 2026, 1, 15, 0, 30)
    ctrl.tick()
    rtc_at(ctrl, 2026, 1, 16, 0, 30)
    ctrl.tick()  # day hook raises, swallowed
    assert ctrl.state == GateState.IDLE_CLOSED
    ctrl.state = GateState.IDLE_OPEN
    ctrl._halt(GateState.SAFETY_STOP)  # on_event raises, swallowed
    assert ctrl.state == GateState.SAFETY_STOP
    ctrl.enter_error()
    assert ctrl.state == GateState.ERROR


# 04 #32
async def test_on_event_kinds_and_last_local_fields(ctrl, vsleep):
    events = []
    ctrl.on_event = lambda kind, trig: events.append((kind, trig, ctrl._last_local))
    ctrl.state = GateState.IDLE_CLOSED
    rtc_at(ctrl, 2026, 1, 15, 12, 0)
    ctrl.tick()  # AUTO open
    await wait_for(lambda: ctrl.nsleep.value() == 1)
    set_limit(ctrl.limit_top, True)
    await wait_for(lambda: ctrl.state == GateState.IDLE_OPEN)
    assert events == [("open", MovementTrigger.AUTO, (2026, 1, 15, 720))]
    ctrl._date = None  # logging must not depend on _date
    ctrl._halt(GateState.SAFETY_STOP)
    assert events[-1] == ("safety_stop", MovementTrigger.AUTO, (2026, 1, 15, 720))
    assert ctrl._date is None


# 04 #33 (controller part; main flush spy is 04 main tests)
def test_error_event_fires_once(ctrl):
    events = []
    ctrl.on_event = lambda kind, trig: events.append((kind, trig))
    ctrl.state = GateState.IDLE_OPEN
    ctrl._trigger = MovementTrigger.MANUAL
    ctrl.enter_error()
    ctrl._halt(GateState.ERROR)
    ctrl.enter_error()
    assert events == [("error", MovementTrigger.AUTO)]


# ---------------------------------------------------------------------------
# status_json
# ---------------------------------------------------------------------------

STATUS_KEYS = {"state", "time", "limit_top", "limit_bottom", "config_warning", "today_schedule", "rtc_synced"}


def test_status_json_keys_and_values_synced(ctrl):
    ctrl.state = GateState.IDLE_CLOSED
    rtc_at(ctrl, 2026, 1, 15, 22, 0)
    ctrl.tick()
    data = json.loads(ctrl.status_json())
    assert set(data) == STATUS_KEYS
    assert data["state"] == GateState.IDLE_CLOSED
    assert data["today_schedule"] == {"open": 360, "close": 1080}
    assert data["rtc_synced"] is True
    assert data["config_warning"] == ""
    assert data["time"] == "22:00"


def test_status_json_reports_unsynced(ctrl):
    ctrl.state = GateState.IDLE_CLOSED
    rtc_at(ctrl, UNSYNCED_YEAR, 1, 1, 12, 0)
    ctrl.tick()
    data = json.loads(ctrl.status_json())
    assert set(data) == STATUS_KEYS
    assert data["rtc_synced"] is False
    assert data["time"] is None
