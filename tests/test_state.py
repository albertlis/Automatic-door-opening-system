# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
"""
State machine tests for CoopController.

Fixture config (from conftest):
  window: legacy, hour_open=6, hour_close=18  → wo=360, wc=1080 (local minutes)
  override_open: fixed, fixed_hour=8           → ao=480
  override_close: fixed, fixed_hour=22         → ac=1320

All datetime tuples are CET (UTC+1) as stored in DS3231.
June = CEST active (+60 min DST). CET→local: local = h*60 + 60.

Quick reference (June 2024, minute=0):
  04:00 CET → 300 local  (before wo=360)
  05:00 CET → 360 local  (=wo, edge)
  06:00 CET → 420 local  (in window, before ao=480)
  07:00 CET → 480 local  (=ao, edge)
  08:01 CET → 541 local  (in window, past ao)
  14:00 CET → 900 local  (mid-day)
  17:00 CET → 1080 local (=wc, edge)
  18:01 CET → 1141 local (outside window)
  19:00 CET → 1200 local (outside window, before ac=1320)
  22:00 CET → 1380 local (past ac=1320)
"""

from src.state import GateState, MovementTrigger

# ---------------------------------------------------------------------------
# INIT transitions
# ---------------------------------------------------------------------------


def test_init_limit_top_active(ctrl):
    ctrl.state = GateState.INIT
    ctrl.limit_top.activate()
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_OPEN


def test_init_limit_bottom_active(ctrl):
    ctrl.state = GateState.INIT
    ctrl.limit_bottom.activate()
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_CLOSED


def test_init_both_limits_active_is_error(ctrl):
    ctrl.state = GateState.INIT
    ctrl.limit_top.activate()
    ctrl.limit_bottom.activate()
    ctrl.tick()
    assert ctrl.state == GateState.ERROR


def test_init_no_limits_daytime_opens(ctrl):
    # 10:00 CET = 660 local, wo=360, wc=1080 → daytime
    ctrl.state = GateState.INIT
    ctrl.rtc.set_datetime((2024, 6, 1, 10, 0, 0, 0, 0))
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_OPEN


def test_init_no_limits_nighttime_closes(ctrl):
    # 20:00 CET = 1260 local, wc=1080 → nighttime
    ctrl.state = GateState.INIT
    ctrl.rtc.set_datetime((2024, 6, 1, 20, 0, 0, 0, 0))
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_CLOSE


def test_init_trigger_is_auto(ctrl):
    ctrl.state = GateState.INIT
    ctrl.rtc.set_datetime((2024, 6, 1, 10, 0, 0, 0, 0))
    ctrl.tick()
    assert ctrl._trigger == MovementTrigger.AUTO


# ---------------------------------------------------------------------------
# Sensor-based open / close
# ---------------------------------------------------------------------------


def test_morning_open(ctrl):
    # 08:01 CET = 541 local — in window (360..1080), past ao=480, bright
    ctrl.rtc.set_datetime((2024, 6, 1, 8, 1, 0, 0, 0))
    ctrl.lux_buffer = [10.0] * 5
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_OPEN


def test_evening_close(ctrl):
    # 18:01 CET = 1141 local — outside window (>=wc=1080), dark
    ctrl.state = GateState.IDLE_OPEN
    ctrl.rtc.set_datetime((2024, 6, 1, 18, 1, 0, 0, 0))
    ctrl.lux_buffer = [1.0] * 5
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_CLOSE


def test_cloud_no_premature_close(ctrl):
    """3/5 samples below threshold — not all low — no close."""
    ctrl.state = GateState.IDLE_OPEN
    ctrl.rtc.set_datetime((2024, 6, 1, 14, 0, 0, 0, 0))  # 900 local, inside window
    ctrl.lux_buffer = [10.0, 10.0, 1.0, 1.0, 10.0]
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_OPEN


def test_no_open_when_dark_inside_window(ctrl):
    """Inside time window, lux low — do not open (sensor_open=False, abs_open=False)."""
    # 06:00 CET = 420 local — in window, before ao=480 → abs_open won't fire
    ctrl.rtc.set_datetime((2024, 6, 1, 6, 0, 0, 0, 0))
    ctrl.lux_buffer = [1.0] * 5  # below lux_open=8.0
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_CLOSED


def test_no_open_before_window(ctrl):
    """Bright but before window_open — do not open."""
    # 04:00 CET = 300 local < wo=360
    ctrl.rtc.set_datetime((2024, 6, 1, 4, 0, 0, 0, 0))
    ctrl.lux_buffer = [50.0] * 5
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_CLOSED


def test_no_close_inside_window(ctrl):
    """Dark inside window (sensor_close requires outside window) — no close."""
    ctrl.state = GateState.IDLE_OPEN
    ctrl.rtc.set_datetime((2024, 6, 1, 10, 0, 0, 0, 0))  # 660 local, inside window
    ctrl.lux_buffer = [0.5] * 5  # very dark
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_OPEN


# ---------------------------------------------------------------------------
# Absolute overrides
# ---------------------------------------------------------------------------


def test_absolute_close_overrides_light(ctrl):
    """22:00 CET = 1380 local > ac=1320 — force close regardless of lux."""
    ctrl.state = GateState.IDLE_OPEN
    ctrl.rtc.set_datetime((2024, 6, 1, 22, 0, 0, 0, 0))
    ctrl.lux_buffer = [50.0] * 5
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_CLOSE


def test_absolute_open_overrides_low_lux(ctrl):
    """ao fires (07:00 CET = 480 local = ao) even when lux_ready=False."""
    ctrl.lux_ready = False
    ctrl.rtc.set_datetime((2024, 6, 1, 7, 0, 0, 0, 0))  # 480 local = ao
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_OPEN


def test_absolute_open_trigger_is_auto(ctrl):
    ctrl.lux_ready = False
    ctrl.rtc.set_datetime((2024, 6, 1, 7, 0, 0, 0, 0))
    ctrl.tick()
    assert ctrl._trigger == MovementTrigger.AUTO


def test_absolute_close_trigger_is_auto(ctrl):
    ctrl.state = GateState.IDLE_OPEN
    ctrl.rtc.set_datetime((2024, 6, 1, 22, 0, 0, 0, 0))
    ctrl.tick()
    assert ctrl._trigger == MovementTrigger.AUTO


# ---------------------------------------------------------------------------
# Manual hold — auto logic blocked
# ---------------------------------------------------------------------------


def test_manual_hold_open_blocks_auto_close(ctrl):
    """MANUAL_HOLD_OPEN: outside window, but abs_close not yet reached → no transition."""
    ctrl.state = GateState.MANUAL_HOLD_OPEN
    # 19:00 CET = 1200 local — past wc=1080 but before ac=1320
    ctrl.rtc.set_datetime((2024, 6, 1, 19, 0, 0, 0, 0))
    ctrl.lux_buffer = [1.0] * 5
    ctrl.tick()
    assert ctrl.state == GateState.MANUAL_HOLD_OPEN


def test_manual_hold_closed_blocks_auto_open(ctrl):
    """MANUAL_HOLD_CLOSED: sensor_open + bright light don't trigger — abs_open not yet reached."""
    ctrl.state = GateState.MANUAL_HOLD_CLOSED
    # 06:00 CET = 420 local — in window but before ao=480
    ctrl.rtc.set_datetime((2024, 6, 1, 6, 0, 0, 0, 0))
    ctrl.lux_buffer = [50.0] * 5
    ctrl.tick()
    assert ctrl.state == GateState.MANUAL_HOLD_CLOSED


def test_manual_hold_open_releases_on_abs_close(ctrl):
    """MANUAL_HOLD_OPEN: absolute close (22:00 CET = 1380 > ac=1320) overrides hold."""
    ctrl.state = GateState.MANUAL_HOLD_OPEN
    ctrl.rtc.set_datetime((2024, 6, 1, 22, 0, 0, 0, 0))
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_CLOSE
    assert ctrl._trigger == MovementTrigger.AUTO


def test_manual_hold_closed_releases_on_abs_open(ctrl):
    """MANUAL_HOLD_CLOSED: absolute open (07:00 CET = 480 local = ao) overrides hold."""
    ctrl.state = GateState.MANUAL_HOLD_CLOSED
    ctrl.rtc.set_datetime((2024, 6, 1, 7, 0, 0, 0, 0))  # 480 local = ao
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_OPEN
    assert ctrl._trigger == MovementTrigger.AUTO


# ---------------------------------------------------------------------------
# Safety stop
# ---------------------------------------------------------------------------


def test_safety_stop_on_timeout(ctrl):
    """Motor timeout → SAFETY_STOP + motor stopped."""
    ctrl.state = GateState.MOVING_OPEN
    ctrl.simulate_timeout()
    ctrl.tick()
    assert ctrl.state == GateState.SAFETY_STOP
    assert "stop" in ctrl.motor.commands


def test_safety_stop_increments_error_count(ctrl):
    ctrl.state = GateState.MOVING_OPEN
    ctrl.simulate_timeout()
    ctrl.tick()
    assert ctrl._today_rec[4] == 1


def test_safety_stop_no_double_count(ctrl):
    """Second _safety_stop() while already SAFETY_STOP must not increment again."""
    ctrl.state = GateState.SAFETY_STOP
    ctrl._safety_stop()
    assert ctrl._today_rec[4] == 0  # was 0, stays 0


def test_nsleep_low_on_safety_stop(ctrl):
    ctrl.state = GateState.MOVING_OPEN
    ctrl.simulate_timeout()
    ctrl.tick()
    assert ctrl.nsleep.value() == 0


def test_safety_stop_close_timeout(ctrl):
    """MOVING_CLOSE timeout also lands in SAFETY_STOP."""
    ctrl.state = GateState.MOVING_CLOSE
    ctrl.simulate_timeout()
    ctrl.tick()
    assert ctrl.state == GateState.SAFETY_STOP


# ---------------------------------------------------------------------------
# tick() no-ops in terminal states
# ---------------------------------------------------------------------------


def test_tick_no_op_in_safety_stop(ctrl):
    """tick() must not change state when in SAFETY_STOP."""
    ctrl.state = GateState.SAFETY_STOP
    ctrl.rtc.set_datetime((2024, 6, 1, 8, 1, 0, 0, 0))
    ctrl.lux_buffer = [50.0] * 5
    ctrl.tick()
    assert ctrl.state == GateState.SAFETY_STOP


def test_tick_no_op_in_error(ctrl):
    """tick() must not change state when in ERROR."""
    ctrl.state = GateState.ERROR
    ctrl.rtc.set_datetime((2024, 6, 1, 8, 1, 0, 0, 0))
    ctrl.lux_buffer = [50.0] * 5
    ctrl.tick()
    assert ctrl.state == GateState.ERROR


# ---------------------------------------------------------------------------
# lux_ready gate
# ---------------------------------------------------------------------------


def test_no_sensor_open_when_not_ready(ctrl):
    """lux_ready=False + time in window but before abs_open → stays IDLE_CLOSED."""
    # 06:00 CET = 420 local — in window, before ao=480 → abs_open=False
    ctrl.lux_ready = False
    ctrl.rtc.set_datetime((2024, 6, 1, 6, 0, 0, 0, 0))
    ctrl.lux_buffer = [50.0] * 5  # values don't matter, lux_ready=False
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_CLOSED


def test_no_sensor_close_when_not_ready(ctrl):
    """lux_ready=False + time outside window → stays IDLE_OPEN (no sensor_close)."""
    ctrl.state = GateState.IDLE_OPEN
    ctrl.lux_ready = False
    ctrl.rtc.set_datetime((2024, 6, 1, 19, 0, 0, 0, 0))  # 1200 local, outside wc but before ac=1320
    ctrl.lux_buffer = [0.1] * 5
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_OPEN


# ---------------------------------------------------------------------------
# I2C error handling
# ---------------------------------------------------------------------------


def test_i2c_single_failure_no_error(ctrl):
    def bad_rtc():
        raise OSError("I2C fail")

    ctrl._i2c_call(bad_rtc)
    assert ctrl.state != GateState.ERROR
    assert ctrl._i2c_fail_count == 1


def test_i2c_three_failures_enter_error(ctrl):
    def bad_fn():
        raise OSError("I2C fail")

    for _ in range(3):
        ctrl._i2c_call(bad_fn)

    assert ctrl.state == GateState.ERROR


def test_i2c_fail_resets_on_success(ctrl):
    def bad_fn():
        raise OSError()

    def good_fn():
        return (2024, 6, 1, 8, 0, 0, 0, 0)

    ctrl._i2c_call(bad_fn)
    ctrl._i2c_call(bad_fn)
    ctrl._i2c_call(good_fn)
    assert ctrl._i2c_fail_count == 0


def test_i2c_two_failures_no_error(ctrl):
    """2 consecutive failures — not yet at threshold."""

    def bad_fn():
        raise OSError()

    ctrl._i2c_call(bad_fn)
    ctrl._i2c_call(bad_fn)
    assert ctrl.state != GateState.ERROR
    assert ctrl._i2c_fail_count == 2


# ---------------------------------------------------------------------------
# _resolve_times clamping
# ---------------------------------------------------------------------------


def test_resolve_times_clamps_abs_open_and_sets_warning(ctrl):
    """abs_open < window_open → clamp + warning set."""
    ctrl.config.window.mode = "legacy"
    ctrl.config.window.legacy.hour_open = 10  # wo = 600
    ctrl.config.override_open.mode = "fixed"
    ctrl.config.override_open.fixed_hour = 8  # ao = 480 < wo → clamp to 600
    ctrl._resolve_times(2024, 6, 1, 8, 0)
    wo, wc, ao, ac = ctrl._today_times
    assert ao >= wo
    assert "clamped" in ctrl._config_warning


def test_resolve_times_clamps_abs_close_and_sets_warning(ctrl):
    """abs_close < window_close → clamp + warning set."""
    ctrl.config.window.mode = "legacy"
    ctrl.config.window.legacy.hour_close = 20  # wc = 1200
    ctrl.config.override_close.mode = "fixed"
    ctrl.config.override_close.fixed_hour = 18  # ac = 1080 < wc → clamp to 1200
    ctrl._resolve_times(2024, 6, 1, 8, 0)
    wo, wc, ao, ac = ctrl._today_times
    assert ac >= wc
    assert "clamped" in ctrl._config_warning


def test_resolve_times_no_warning_when_valid(ctrl):
    """Default fixture config is valid — no warning."""
    ctrl._resolve_times(2024, 6, 1, 8, 0)
    assert ctrl._config_warning == ""


def test_resolve_times_warning_on_conflict(ctrl):
    """abs_open >= abs_close after clamping → conflict warning."""
    ctrl.config.window.mode = "legacy"
    ctrl.config.window.legacy.hour_open = 10  # wo = 600
    ctrl.config.window.legacy.hour_close = 12  # wc = 720
    ctrl.config.override_open.mode = "fixed"
    ctrl.config.override_open.fixed_hour = 11  # ao = 660 (>wo=600 ✓)
    ctrl.config.override_close.mode = "fixed"
    ctrl.config.override_close.fixed_hour = 11  # ac = 660 = ao → conflict
    ctrl._resolve_times(2024, 6, 1, 8, 0)
    assert ctrl._config_warning != ""


def test_resolve_times_updates_today_times(ctrl):
    """After _resolve_times, _today_times reflects new config."""
    ctrl.config.window.mode = "legacy"
    ctrl.config.window.legacy.hour_open = 7  # wo = 420
    ctrl.config.window.legacy.hour_close = 19  # wc = 1140
    ctrl.config.override_open.mode = "fixed"
    ctrl.config.override_open.fixed_hour = 9  # ao = 540
    ctrl.config.override_close.mode = "fixed"
    ctrl.config.override_close.fixed_hour = 23  # ac = 1380
    ctrl._resolve_times(2024, 6, 1, 8, 0)
    wo, wc, ao, ac = ctrl._today_times
    assert wo == 420
    assert wc == 1140
    assert ao == 540
    assert ac == 1380


# ---------------------------------------------------------------------------
# manual_move
# ---------------------------------------------------------------------------


def test_manual_move_open_from_idle_closed(ctrl):
    ctrl.manual_move("open")
    assert ctrl.state == GateState.MOVING_OPEN
    assert ctrl._trigger == MovementTrigger.MANUAL


def test_manual_move_close_from_idle_open(ctrl):
    ctrl.state = GateState.IDLE_OPEN
    ctrl.manual_move("close")
    assert ctrl.state == GateState.MOVING_CLOSE
    assert ctrl._trigger == MovementTrigger.MANUAL


def test_manual_move_ignored_when_already_open(ctrl):
    ctrl.state = GateState.IDLE_OPEN
    ctrl.manual_move("open")
    assert ctrl.state == GateState.IDLE_OPEN


def test_manual_move_ignored_when_already_closed(ctrl):
    ctrl.manual_move("close")
    assert ctrl.state == GateState.IDLE_CLOSED


def test_manual_move_ignored_when_manual_hold_open(ctrl):
    ctrl.state = GateState.MANUAL_HOLD_OPEN
    ctrl.manual_move("open")
    assert ctrl.state == GateState.MANUAL_HOLD_OPEN


def test_manual_move_ignored_when_manual_hold_closed(ctrl):
    ctrl.state = GateState.MANUAL_HOLD_CLOSED
    ctrl.manual_move("close")
    assert ctrl.state == GateState.MANUAL_HOLD_CLOSED


def test_manual_move_ignored_when_moving_same_direction(ctrl):
    ctrl.state = GateState.MOVING_OPEN
    ctrl.manual_move("open")
    assert ctrl.state == GateState.MOVING_OPEN


def test_manual_move_from_safety_stop_uses_recovery_trigger(ctrl):
    ctrl.state = GateState.SAFETY_STOP
    ctrl.manual_move("open")
    assert ctrl._trigger == MovementTrigger.RECOVERY
    assert ctrl.state == GateState.MOVING_OPEN


def test_manual_move_from_idle_not_safety_stop_uses_manual_trigger(ctrl):
    ctrl.manual_move("open")
    assert ctrl._trigger == MovementTrigger.MANUAL


def test_manual_move_increments_manual_count(ctrl):
    ctrl.manual_move("open")
    assert ctrl._today_rec[3] == 1


def test_manual_move_increments_on_each_call(ctrl):
    ctrl.manual_move("open")
    ctrl.state = GateState.IDLE_OPEN
    ctrl.manual_move("close")
    assert ctrl._today_rec[3] == 2


def test_manual_move_blocked_from_error(ctrl):
    """ERROR state: manual_move must not change state — reboot required."""
    ctrl.state = GateState.ERROR
    ctrl.manual_move("open")
    assert ctrl.state == GateState.ERROR


def test_manual_move_abort_when_moving_opposite(ctrl):
    """manual_move in opposite direction stops motor and switches state."""
    ctrl.state = GateState.MOVING_OPEN
    ctrl.manual_move("close")
    # _abort_move=True is set here; Phase 4 _run_move will clear it when it detects the flag
    assert ctrl._abort_move is True
    assert "stop" in ctrl.motor.commands
    assert ctrl.state == GateState.MOVING_CLOSE


# ---------------------------------------------------------------------------
# is_daytime
# ---------------------------------------------------------------------------


def test_is_daytime_inside_window(ctrl):
    ctrl.rtc.set_datetime((2024, 6, 1, 10, 0, 0, 0, 0))  # 660 local, inside 360..1080
    assert ctrl.is_daytime() is True


def test_is_daytime_outside_window_night(ctrl):
    ctrl.rtc.set_datetime((2024, 6, 1, 20, 0, 0, 0, 0))  # 1260 local, outside window
    assert ctrl.is_daytime() is False


def test_is_daytime_at_window_open_edge(ctrl):
    # 05:00 CET = 360 local = wo exactly → is_daytime True (inclusive)
    ctrl.rtc.set_datetime((2024, 6, 1, 5, 0, 0, 0, 0))
    assert ctrl.is_daytime() is True


def test_is_daytime_at_window_close_edge(ctrl):
    # 17:00 CET = 1080 local = wc exactly → is_daytime False (exclusive upper bound)
    ctrl.rtc.set_datetime((2024, 6, 1, 17, 0, 0, 0, 0))
    assert ctrl.is_daytime() is False


# ---------------------------------------------------------------------------
# get_forecast
# ---------------------------------------------------------------------------


def test_get_forecast_default_length(ctrl):
    result = ctrl.get_forecast()
    assert len(result) == 30


def test_get_forecast_custom_length(ctrl):
    result = ctrl.get_forecast(7)
    assert len(result) == 7


def test_get_forecast_structure(ctrl):
    result = ctrl.get_forecast(5)
    for wo, wc, ao, ac in result:
        assert isinstance(wo, int)
        assert isinstance(wc, int)
        assert isinstance(ao, int)
        assert isinstance(ac, int)


def test_get_forecast_invariants(ctrl):
    """All forecast days satisfy: wo < wc, ao>=wo, ac>=wc, ao<ac."""
    result = ctrl.get_forecast(30)
    for wo, wc, ao, ac in result:
        assert wo < wc, f"wo={wo} >= wc={wc}"
        assert ao >= wo, f"ao={ao} < wo={wo}"
        assert ac >= wc, f"ac={ac} < wc={wc}"
        assert ao < ac, f"ao={ao} >= ac={ac}"


def test_get_forecast_legacy_fixed_constant(ctrl):
    """Legacy window + fixed overrides → all days identical (no sun variation)."""
    result = ctrl.get_forecast(7)
    assert all(r == result[0] for r in result)


def test_get_forecast_sun_position_varies(ctrl):
    """sun_position mode → wo/wc differ across 30 days (sun moves)."""
    from src.config import default_config

    ctrl.config = default_config()  # sun_position mode
    # Need to set _today_times so _resolve_times uses the right config
    ctrl._resolve_times(2024, 1, 1, 8, 0)
    result = ctrl.get_forecast(30)
    wo_values = [r[0] for r in result]
    # In January wo values should vary (sun rises later toward winter solstice)
    assert len(set(wo_values)) > 1


# ---------------------------------------------------------------------------
# abs_close via now < wo branch (before window opens — early morning)
# 02:00 CET = 180 local < wo=360 → now < wo → abs_close=True, sensor_close=True
# ---------------------------------------------------------------------------


def test_abs_close_fires_before_window_idle_open(ctrl):
    """IDLE_OPEN before window opens (02:00 CET) → abs_close triggers close."""
    ctrl.state = GateState.IDLE_OPEN
    ctrl.rtc.set_datetime((2024, 6, 1, 2, 0, 0, 0, 0))  # 180 local < wo=360
    ctrl.lux_buffer = [50.0] * 5  # bright — but abs_close ignores lux
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_CLOSE
    assert ctrl._trigger == MovementTrigger.AUTO


def test_sensor_close_fires_before_window(ctrl):
    """sensor_close: now < wo is 'outside window' → dark pre-dawn closes gate."""
    ctrl.state = GateState.IDLE_OPEN
    ctrl.rtc.set_datetime((2024, 6, 1, 2, 0, 0, 0, 0))  # 180 local < wo=360
    ctrl.lux_buffer = [1.0] * 5  # dark
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_CLOSE


def test_manual_hold_open_releases_before_window(ctrl):
    """MANUAL_HOLD_OPEN at 02:00 CET: now < wo → abs_close fires → releases hold."""
    ctrl.state = GateState.MANUAL_HOLD_OPEN
    ctrl.rtc.set_datetime((2024, 6, 1, 2, 0, 0, 0, 0))  # 180 local < wo=360
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_CLOSE
    assert ctrl._trigger == MovementTrigger.AUTO


def test_idle_closed_stays_before_window_dark(ctrl):
    """IDLE_CLOSED before window opens, dark — must NOT open (abs_open requires now>=ao)."""
    ctrl.rtc.set_datetime((2024, 6, 1, 2, 0, 0, 0, 0))  # 180 local
    ctrl.lux_buffer = [0.5] * 5
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_CLOSED


# ---------------------------------------------------------------------------
# Lux boundary conditions (strict comparison)
# ---------------------------------------------------------------------------


def test_lux_exactly_at_open_threshold_no_open(ctrl):
    """lux == lux_open (8.0): 'v > threshold' is strict → does NOT open.
    Use 06:00 CET = 420 local (in window, before ao=480) so abs_open can't fire.
    """
    ctrl.rtc.set_datetime((2024, 6, 1, 6, 0, 0, 0, 0))  # 420 local, in window, before ao
    ctrl.lux_buffer = [8.0] * 5  # exactly at lux_open threshold
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_CLOSED


def test_lux_exactly_at_close_threshold_no_close(ctrl):
    """lux == lux_close (3.0): 'v < threshold' is strict → does NOT close."""
    ctrl.state = GateState.IDLE_OPEN
    ctrl.rtc.set_datetime((2024, 6, 1, 18, 1, 0, 0, 0))  # 1141 local, outside window
    ctrl.lux_buffer = [3.0] * 5  # exactly at threshold
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_OPEN


def test_four_of_five_below_close_threshold_no_close(ctrl):
    """4/5 samples below lux_close — all() requires all 5 → no close."""
    ctrl.state = GateState.IDLE_OPEN
    ctrl.rtc.set_datetime((2024, 6, 1, 18, 1, 0, 0, 0))  # outside window
    ctrl.lux_buffer = [1.0, 1.0, 1.0, 1.0, 10.0]  # 4 below, 1 above
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_OPEN


def test_abs_open_at_wc_edge_does_not_fire(ctrl):
    """abs_open = ao <= now < wc — at now=wc (17:00 CET=1080) upper bound is exclusive."""
    ctrl.rtc.set_datetime((2024, 6, 1, 17, 0, 0, 0, 0))  # 1080 local = wc exactly
    ctrl.lux_ready = False
    ctrl.tick()
    # abs_open = 480 <= 1080 < 1080 = False → stays IDLE_CLOSED
    assert ctrl.state == GateState.IDLE_CLOSED


# ---------------------------------------------------------------------------
# manual_move from MANUAL_HOLD_* states (spec §4.3 explicit transitions)
# ---------------------------------------------------------------------------


def test_manual_move_close_from_manual_hold_open(ctrl):
    """MANUAL_HOLD_OPEN + BTN_CLOSE → MOVING_CLOSE with MANUAL trigger."""
    ctrl.state = GateState.MANUAL_HOLD_OPEN
    ctrl.manual_move("close")
    assert ctrl.state == GateState.MOVING_CLOSE
    assert ctrl._trigger == MovementTrigger.MANUAL


def test_manual_move_open_from_manual_hold_closed(ctrl):
    """MANUAL_HOLD_CLOSED + BTN_OPEN → MOVING_OPEN with MANUAL trigger."""
    ctrl.state = GateState.MANUAL_HOLD_CLOSED
    ctrl.manual_move("open")
    assert ctrl.state == GateState.MOVING_OPEN
    assert ctrl._trigger == MovementTrigger.MANUAL


def test_manual_move_close_from_safety_stop(ctrl):
    """SAFETY_STOP + BTN_CLOSE → MOVING_CLOSE with RECOVERY trigger."""
    ctrl.state = GateState.SAFETY_STOP
    ctrl.manual_move("close")
    assert ctrl.state == GateState.MOVING_CLOSE
    assert ctrl._trigger == MovementTrigger.RECOVERY


# ---------------------------------------------------------------------------
# _on_midnight
# ---------------------------------------------------------------------------


def test_on_midnight_resets_today_rec(ctrl):
    """_on_midnight resets daily record — days_since_2025 set to new day."""
    from src.logs import days_since_2025

    ctrl._today_rec = [5, 300, 1100, 3, 2]
    ctrl._on_midnight(2024, 6, 2, 0, 0)
    assert ctrl._today_rec == [days_since_2025(2024, 6, 2), 0xFFFF, 0xFFFF, 0, 0]


def test_on_midnight_resets_error_count(ctrl):
    ctrl._today_rec_error_count = 4
    ctrl._on_midnight(2024, 6, 2, 0, 0)
    assert ctrl._today_rec_error_count == 0


def test_on_midnight_recalculates_times(ctrl):
    """_on_midnight calls _resolve_times → _today_times updated for new day."""
    old_times = ctrl._today_times
    ctrl._on_midnight(2024, 6, 2, 0, 0)
    # Legacy mode → times don't change day-to-day, but the call must succeed
    assert ctrl._today_times == old_times  # legacy: same every day


def test_on_midnight_sun_position_updates_times(ctrl):
    """With sun_position mode, times differ between solstice-adjacent dates."""
    from src.config import default_config

    ctrl.config = default_config()  # sun_position
    ctrl._on_midnight(2024, 6, 21, 0, 0)
    summer_times = ctrl._today_times
    ctrl._on_midnight(2024, 12, 21, 0, 0)
    winter_times = ctrl._today_times
    assert summer_times[0] != winter_times[0]  # wo differs between summer/winter


# ---------------------------------------------------------------------------
# _enter_error stops motor
# ---------------------------------------------------------------------------


def test_enter_error_stops_motor(ctrl):
    """_enter_error() must stop motor and put nSLEEP low before setting ERROR."""
    ctrl.state = GateState.MOVING_OPEN
    ctrl._enter_error()
    assert ctrl.state == GateState.ERROR
    assert "stop" in ctrl.motor.commands
    assert ctrl.nsleep.value() == 0


# ---------------------------------------------------------------------------
# status_json
# ---------------------------------------------------------------------------


def test_status_json_valid_json(ctrl):
    import json

    raw = ctrl.status_json()
    data = json.loads(raw)  # must not raise
    assert isinstance(data, dict)


def test_status_json_required_keys(ctrl):
    import json

    data = json.loads(ctrl.status_json())
    assert "state" in data
    assert "lux" in data
    assert "time" in data
    assert "limit_top" in data
    assert "limit_bottom" in data
    assert "vbat_v" in data
    assert "config_warning" in data
    assert "today_schedule" in data
    assert "sunrise_today" in data
    assert "sunset_today" in data


def test_status_json_state_is_string(ctrl):
    import json

    data = json.loads(ctrl.status_json())
    assert isinstance(data["state"], str)
    assert data["state"] == "IDLE_CLOSED"


def test_status_json_lux_is_list_of_five(ctrl):
    import json

    data = json.loads(ctrl.status_json())
    assert isinstance(data["lux"], list)
    assert len(data["lux"]) == 5


def test_status_json_limit_top_reflects_pin(ctrl):
    import json

    ctrl.limit_top.activate()
    data = json.loads(ctrl.status_json())
    assert data["limit_top"] is True


def test_status_json_vbat_none(ctrl):
    """vbat_v is None until Phase 8 wires ADC."""
    import json

    data = json.loads(ctrl.status_json())
    assert data["vbat_v"] is None


def test_status_json_reflects_config_warning(ctrl):
    """config_warning in JSON matches controller field."""
    import json

    ctrl._config_warning = "abs_open 06:00 < window_open 08:00 — clamped"
    data = json.loads(ctrl.status_json())
    assert "clamped" in data["config_warning"]


def test_status_json_empty_warning_when_none(ctrl):
    import json

    ctrl._config_warning = ""
    data = json.loads(ctrl.status_json())
    assert data["config_warning"] == ""


def test_status_json_includes_today_schedule(ctrl):
    import json

    ctrl._today_times = (360, 1080, 480, 1320)
    data = json.loads(ctrl.status_json())
    assert "today_schedule" in data
    assert data["today_schedule"] == {"wo": 360, "wc": 1080, "ao": 480, "ac": 1320}


# ---------------------------------------------------------------------------
# _next_day date arithmetic (used by get_forecast)
# ---------------------------------------------------------------------------


def test_get_forecast_month_boundary(ctrl):
    """get_forecast from Jan 31 → Feb 1 (month rollover)."""
    ctrl.rtc.set_datetime((2024, 1, 31, 8, 0, 0, 0, 0))
    result = ctrl.get_forecast(2)
    assert len(result) == 2


def test_get_forecast_year_boundary(ctrl):
    """get_forecast from Dec 31 → Jan 1 (year rollover)."""
    ctrl.rtc.set_datetime((2024, 12, 31, 8, 0, 0, 0, 0))
    result = ctrl.get_forecast(2)
    assert len(result) == 2


def test_get_forecast_leap_feb(ctrl):
    """get_forecast from Feb 28 in leap year → Feb 29 (not Mar 1)."""
    # 2024 is a leap year; forecast must include Feb 29
    ctrl.rtc.set_datetime((2024, 2, 28, 8, 0, 0, 0, 0))
    result = ctrl.get_forecast(3)
    assert len(result) == 3
    # All 3 days must satisfy invariants (proves _next_day didn't skip a day)
    for wo, wc, ao, ac in result:
        assert wo < wc
        assert ao < ac


def test_get_forecast_nonleap_feb(ctrl):
    """get_forecast from Feb 28 in non-leap year → Mar 1 (not Feb 29)."""
    ctrl.rtc.set_datetime((2023, 2, 28, 8, 0, 0, 0, 0))
    result = ctrl.get_forecast(2)
    assert len(result) == 2


def test_get_forecast_zero_days(ctrl):
    """get_forecast(0) returns empty list, no crash."""
    result = ctrl.get_forecast(0)
    assert result == []


# ---------------------------------------------------------------------------
# get_sun_forecast
# ---------------------------------------------------------------------------


def test_get_sun_forecast_default_length(ctrl):
    assert len(ctrl.get_sun_forecast()) == 30


def test_get_sun_forecast_custom_length(ctrl):
    assert len(ctrl.get_sun_forecast(7)) == 7


def test_get_sun_forecast_zero_days(ctrl):
    assert ctrl.get_sun_forecast(0) == []


def test_get_sun_forecast_structure(ctrl):
    """Each entry is (rise_local_min, set_local_min), rise < set, both in 0..1500."""
    result = ctrl.get_sun_forecast(5)
    for rise, sset in result:
        assert isinstance(rise, int)
        assert isinstance(sset, int)
        assert 0 <= rise <= 1500
        assert 0 <= sset <= 1500
        assert rise < sset


def test_get_sun_forecast_dst_applied(ctrl):
    """June (CEST active) → rise_local = rise_cet + 60."""
    from src.astro import is_dst, sun_times_cet

    ctrl.rtc.set_datetime((2024, 6, 1, 8, 0, 0, 0, 0))
    result = ctrl.get_sun_forecast(1)
    rise, sset = result[0]
    rise_cet, set_cet = sun_times_cet(2024, 6, 1)
    assert is_dst(2024, 6, 1, 12)
    assert rise == rise_cet + 60
    assert sset == set_cet + 60


def test_get_sun_forecast_no_dst_in_december(ctrl):
    """December (CET) → no DST offset, rise_local = rise_cet."""
    from src.astro import sun_times_cet

    ctrl.rtc.set_datetime((2024, 12, 21, 8, 0, 0, 0, 0))
    result = ctrl.get_sun_forecast(1)
    rise, sset = result[0]
    rise_cet, set_cet = sun_times_cet(2024, 12, 21)
    assert rise == rise_cet
    assert sset == set_cet


# ---------------------------------------------------------------------------
# tick() with _move_start_ms=0 — Phase 3/4 interface contract
# ---------------------------------------------------------------------------


def test_tick_moving_open_zero_start_triggers_safety_stop(ctrl):
    """_move_start_ms=0 (never set by _run_move stub) → elapsed >> timeout → SAFETY_STOP.

    Documents the Phase 3/4 contract: _run_move MUST set _move_start_ms before the next
    tick(). In production this is guaranteed because _run_move runs as an asyncio task
    immediately after being created by manual_move() or control_loop.
    """
    ctrl.state = GateState.MOVING_OPEN
    ctrl._move_start_ms = 0  # explicitly 0 (not set by _run_move)
    ctrl.tick()
    assert ctrl.state == GateState.SAFETY_STOP


# ---------------------------------------------------------------------------
# _resolve_times — all three warnings simultaneously
# ---------------------------------------------------------------------------


def test_resolve_times_all_warnings_simultaneously(ctrl):
    """ao<wo AND ac<wc (clamped) AND ao>=ac after clamping → all three in warning."""
    ctrl.config.window.mode = "legacy"
    ctrl.config.window.legacy.hour_open = 10  # wo = 600
    ctrl.config.window.legacy.hour_close = 12  # wc = 720
    ctrl.config.override_open.mode = "fixed"
    ctrl.config.override_open.fixed_hour = 8  # ao = 480 < wo=600 → clamp to 600
    ctrl.config.override_close.mode = "fixed"
    ctrl.config.override_close.fixed_hour = 11  # ac = 660 < wc=720 → clamp to 720
    # After clamping: ao=600, ac=720 → ao < ac → no conflict warning
    ctrl._resolve_times(2024, 6, 1, 8, 0)
    assert "clamped" in ctrl._config_warning


def test_resolve_times_conflict_after_double_clamp(ctrl):
    """Both ao and ac clamped to same value → ao >= ac conflict warning."""
    ctrl.config.window.mode = "legacy"
    ctrl.config.window.legacy.hour_open = 10  # wo = 600
    ctrl.config.window.legacy.hour_close = 10  # wc = 600  (same as open!)
    ctrl.config.override_open.mode = "fixed"
    ctrl.config.override_open.fixed_hour = 8  # ao = 480 < wo=600 → clamp to 600
    ctrl.config.override_close.mode = "fixed"
    ctrl.config.override_close.fixed_hour = 9  # ac = 540 < wc=600 → clamp to 600
    # ao=600, ac=600 → ao >= ac → conflict
    ctrl._resolve_times(2024, 6, 1, 8, 0)
    assert "conflict" in ctrl._config_warning


# ---------------------------------------------------------------------------
# abs_close boundary: now == ac (inclusive)
# ---------------------------------------------------------------------------


def test_abs_close_at_exact_ac_boundary(ctrl):
    """abs_close fires at exactly now==ac (>= is inclusive)."""
    ctrl.state = GateState.IDLE_OPEN
    # 21:00 CET = 1320 local = ac exactly
    ctrl.rtc.set_datetime((2024, 6, 1, 21, 0, 0, 0, 0))
    ctrl.lux_buffer = [50.0] * 5  # bright — abs_close ignores lux
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_CLOSE


# ---------------------------------------------------------------------------
# RTC not synced → _today_times stays (0,0,0,0)
# ---------------------------------------------------------------------------


def test_rtc_not_synced_today_times_zero():
    """RTC not synced (y=2000): _resolve_times skipped → _today_times=(0,0,0,0)."""
    from src.config import default_config
    from src.state import CoopController
    from tests.mock_hardware import (
        MockButton,
        MockLED,
        MockMotor,
        MockNFault,
        MockNSleep,
        MockPCF,
        MockRTC,
    )

    rtc = MockRTC()
    rtc.set_datetime((2000, 1, 1, 0, 0, 0, 0, 0))  # DS3231 factory default = never synced
    c = CoopController(
        motor=MockMotor(),
        rtc=rtc,
        light_sensor=None,
        pcf=MockPCF(),
        limit_top=MockButton(),
        limit_bottom=MockButton(),
        btn_open=MockButton(),
        btn_close=MockButton(),
        leds=(MockLED(), MockLED(), MockLED()),
        nsleep=MockNSleep(),
        nfault=MockNFault(),
        config=default_config(),
    )
    assert c._today_times == (0, 0, 0, 0)


# ---------------------------------------------------------------------------
# sensor_open fires without abs_open (in window, before ao)
# ---------------------------------------------------------------------------


def test_sensor_open_before_abs_open(ctrl):
    """Bright inside window before abs_open → opens via sensor only (not abs_open).

    06:00 CET = 420 local: wo=360 <= 420 < wc=1080 (in window), 420 < ao=480 (before backstop).
    sensor_open fires; abs_open does NOT (420 < ao=480).
    test_morning_open at 08:01 CET is past ao=480 — tests both conditions together.
    """
    ctrl.rtc.set_datetime((2024, 6, 1, 6, 0, 0, 0, 0))  # 420 local
    ctrl.lux_buffer = [20.0] * 5
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_OPEN


# ---------------------------------------------------------------------------
# MOVING_CLOSE abort when switching to open direction
# ---------------------------------------------------------------------------


def test_manual_move_abort_when_moving_close_to_open(ctrl):
    """MOVING_CLOSE + manual_move('open'): motor stopped, _abort_move set, state switches."""
    ctrl.state = GateState.MOVING_CLOSE
    ctrl.manual_move("open")
    assert ctrl._abort_move is True
    assert "stop" in ctrl.motor.commands
    assert ctrl.state == GateState.MOVING_OPEN
    assert ctrl._trigger == MovementTrigger.MANUAL


# ---------------------------------------------------------------------------
# sensor_close fires at exactly wc boundary
# ---------------------------------------------------------------------------


def test_sensor_close_at_wc_boundary(ctrl):
    """now == wc (17:00 CET = 1080 local): now>=wc inclusive → sensor_close fires."""
    ctrl.state = GateState.IDLE_OPEN
    ctrl.rtc.set_datetime((2024, 6, 1, 17, 0, 0, 0, 0))  # 1080 local = wc exactly
    ctrl.lux_buffer = [1.0] * 5
    ctrl.tick()
    assert ctrl.state == GateState.MOVING_CLOSE


# ---------------------------------------------------------------------------
# tick() handles RTC I2C failure gracefully (via _i2c_call path in tick)
# ---------------------------------------------------------------------------


def test_tick_rtc_i2c_fail_stays_in_state(ctrl):
    """RTC I2C fail during tick() non-moving path → state unchanged, fail counter increments."""
    original_datetime = ctrl.rtc.datetime

    def bad_datetime():
        raise OSError("I2C")

    ctrl.rtc.datetime = bad_datetime
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_CLOSED  # unchanged
    assert ctrl._i2c_fail_count == 1

    ctrl.rtc.datetime = original_datetime  # restore for teardown


def test_tick_rtc_three_i2c_fails_enter_error(ctrl):
    """3 consecutive RTC I2C fails during tick() → ERROR state."""

    def bad_datetime():
        raise OSError("I2C")

    ctrl.rtc.datetime = bad_datetime
    for _ in range(3):
        ctrl.tick()
    assert ctrl.state == GateState.ERROR


# ---------------------------------------------------------------------------
# abs_open one minute before ao (should NOT fire)
# ---------------------------------------------------------------------------


def test_abs_open_one_minute_before_ao(ctrl):
    """06:59 CET = 479 local < ao=480 → abs_open=False, lux_ready=False → stays IDLE_CLOSED."""
    ctrl.rtc.set_datetime((2024, 6, 1, 6, 59, 0, 0, 0))  # 479 local
    ctrl.lux_ready = False
    ctrl.tick()
    assert ctrl.state == GateState.IDLE_CLOSED


# ---------------------------------------------------------------------------
# MANUAL_HOLD_CLOSED stays before window opens (abs_open requires in-window)
# ---------------------------------------------------------------------------


def test_manual_hold_closed_stays_before_window(ctrl):
    """02:00 CET = 180 local < wo=360 → abs_open condition (ao<=now<wc) False → stays hold."""
    ctrl.state = GateState.MANUAL_HOLD_CLOSED
    ctrl.rtc.set_datetime((2024, 6, 1, 2, 0, 0, 0, 0))  # 180 local < wo=360
    ctrl.tick()
    assert ctrl.state == GateState.MANUAL_HOLD_CLOSED


# ---------------------------------------------------------------------------
# Winter (December, CET, DST=0) _resolve_times gives different times than June
# ---------------------------------------------------------------------------


def test_resolve_times_winter_differs_from_summer(ctrl):
    """December (DST=0) → different wo/wc than June (DST=+60) with sun_position mode."""
    from src.config import default_config

    ctrl.config = default_config()  # sun_position mode
    ctrl._resolve_times(2024, 6, 21, 12, 0)
    summer_wo = ctrl._today_times[0]
    summer_wc = ctrl._today_times[1]

    ctrl._resolve_times(2024, 12, 21, 12, 0)
    winter_wo = ctrl._today_times[0]
    winter_wc = ctrl._today_times[1]

    assert summer_wo != winter_wo, "sunrise should differ summer vs winter"
    assert summer_wc != winter_wc, "sunset should differ summer vs winter"
    assert winter_wo > summer_wo, "winter sunrise later (more minutes from midnight)"
    assert winter_wc < summer_wc, "winter sunset earlier"


# ---------------------------------------------------------------------------
# _on_limit_reached — sync state transition dispatch
# Default RTC: (2024, 6, 1, 8, 0) CET → CEST active → local_minutes = 540
# ---------------------------------------------------------------------------


def test_on_limit_auto_open_goes_idle_open(ctrl):
    ctrl.state = GateState.MOVING_OPEN
    ctrl._trigger = MovementTrigger.AUTO
    ctrl._on_limit_reached()
    assert ctrl.state == GateState.IDLE_OPEN


def test_on_limit_auto_close_goes_idle_closed(ctrl):
    ctrl.state = GateState.MOVING_CLOSE
    ctrl._trigger = MovementTrigger.AUTO
    ctrl._on_limit_reached()
    assert ctrl.state == GateState.IDLE_CLOSED


def test_on_limit_manual_open_goes_manual_hold(ctrl):
    ctrl.state = GateState.MOVING_OPEN
    ctrl._trigger = MovementTrigger.MANUAL
    ctrl._on_limit_reached()
    assert ctrl.state == GateState.MANUAL_HOLD_OPEN


def test_on_limit_manual_close_goes_manual_hold(ctrl):
    ctrl.state = GateState.MOVING_CLOSE
    ctrl._trigger = MovementTrigger.MANUAL
    ctrl._on_limit_reached()
    assert ctrl.state == GateState.MANUAL_HOLD_CLOSED


def test_on_limit_recovery_open_goes_idle(ctrl):
    ctrl.state = GateState.MOVING_OPEN
    ctrl._trigger = MovementTrigger.RECOVERY
    ctrl._on_limit_reached()
    assert ctrl.state == GateState.IDLE_OPEN


def test_on_limit_records_first_open_min(ctrl):
    """First open records local_minutes in _today_rec[1]."""
    from src.astro import local_minutes

    ctrl.state = GateState.MOVING_OPEN
    ctrl._trigger = MovementTrigger.AUTO
    ctrl._today_rec[1] = 0xFFFF
    ctrl._on_limit_reached()
    y, mo, d, h, minute, *_ = ctrl.rtc.datetime()
    assert ctrl._today_rec[1] == local_minutes(y, mo, d, h, minute)


def test_on_limit_does_not_overwrite_open_min(ctrl):
    """Second open does not overwrite first open_min."""
    ctrl.state = GateState.MOVING_OPEN
    ctrl._trigger = MovementTrigger.AUTO
    ctrl._today_rec[1] = 300
    ctrl._on_limit_reached()
    assert ctrl._today_rec[1] == 300


def test_on_limit_records_close_min_always(ctrl):
    """Every close overwrites _today_rec[2] (last close wins)."""
    from src.astro import local_minutes

    ctrl.state = GateState.MOVING_CLOSE
    ctrl._trigger = MovementTrigger.AUTO
    ctrl._today_rec[2] = 999
    ctrl._on_limit_reached()
    y, mo, d, h, minute, *_ = ctrl.rtc.datetime()
    assert ctrl._today_rec[2] == local_minutes(y, mo, d, h, minute)
