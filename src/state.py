# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
from enum import Enum


class GateState(Enum):
    INIT = "INIT"
    IDLE_OPEN = "IDLE_OPEN"
    IDLE_CLOSED = "IDLE_CLOSED"
    MOVING_OPEN = "MOVING_OPEN"
    MOVING_CLOSE = "MOVING_CLOSE"
    MANUAL_HOLD_OPEN = "MANUAL_HOLD_OPEN"
    MANUAL_HOLD_CLOSED = "MANUAL_HOLD_CLOSED"
    SAFETY_STOP = "SAFETY_STOP"
    ERROR = "ERROR"


class MovementTrigger(Enum):
    AUTO = 0
    MANUAL = 1
    RECOVERY = 2


# ---------------------------------------------------------------------------
# Date arithmetic helpers (MicroPython-safe, no time.mktime)
# ---------------------------------------------------------------------------

_MONTH_DAYS = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def _is_leap(y: int) -> bool:
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def _next_day(y: int, m: int, d: int) -> tuple[int, int, int]:
    d += 1
    dim = 29 if m == 2 and _is_leap(y) else _MONTH_DAYS[m]
    if d > dim:
        d = 1
        m += 1
        if m > 12:
            m = 1
            y += 1
    return y, m, d


# ---------------------------------------------------------------------------
# CoopController
# ---------------------------------------------------------------------------


class CoopController:
    def __init__(
        self,
        motor,
        rtc,
        light_sensor,
        pcf,
        limit_top,
        limit_bottom,
        btn_open,
        btn_close,
        leds,  # tuple: (led_red, led_yellow, led_green)
        nsleep,  # DRV8833 nSLEEP pin
        nfault,  # DRV8833 nFAULT pin
        config,
    ):
        self.motor = motor
        self.rtc = rtc
        self.light = light_sensor
        self.pcf = pcf
        self.limit_top = limit_top
        self.limit_bottom = limit_bottom
        self.btn_open = btn_open
        self.btn_close = btn_close
        self.leds = leds
        self.nsleep = nsleep
        self.nfault = nfault
        self.config = config

        self.state: GateState = GateState.INIT
        self._trigger: MovementTrigger = MovementTrigger.AUTO
        self._move_start_ms: int = 0
        self._abort_move: bool = False
        self._i2c_fail_count: int = 0
        self._vbat_low: bool = False
        self._vbat_v: float | None = None
        self._config_warning: str = ""
        self._today_times: tuple = (0, 0, 0, 0)  # (wo, wc, ao, ac) min from midnight

        # Daily record [days_since_2025, open_min, close_min, manual_count, error_count]
        self._today_rec: list = [0, 0xFFFF, 0xFFFF, 0, 0]
        self._today_rec_error_count: int = 0

        # Shared lux buffer (populated by light_sensor_loop in Phase 4)
        self.lux_buffer: list[float] = [0.0] * 5
        self.lux_ready: bool = False

        y, mo, d, h, minute, *_ = self.rtc.datetime()
        if y >= 2020:
            self._resolve_times(y, mo, d, h, minute)
            from src.logs import days_since_2025

            self._today_rec[0] = days_since_2025(y, mo, d)

    # ------------------------------------------------------------------
    # Core sync tick — called every 2s by control_loop (Phase 4)
    # ------------------------------------------------------------------

    def tick(self) -> None:
        from src.compat import ticks_diff, ticks_ms

        if self.state in (GateState.MOVING_OPEN, GateState.MOVING_CLOSE):
            elapsed = ticks_diff(ticks_ms(), self._move_start_ms)
            if elapsed > self.config.safety.move_timeout_s * 1000:
                self._safety_stop()
            return

        result = self._i2c_call(self.rtc.datetime)
        if result is None:
            return  # I2C failed, already transitioned to ERROR
        y, mo, d, h, minute, *_ = result

        wo, wc, ao, ac = self._today_times

        from src.astro import local_minutes

        now = local_minutes(y, mo, d, h, minute)

        lux_ok = self.lux_ready
        all_lux_high = lux_ok and all(v > self.config.light.lux_open for v in self.lux_buffer)
        all_lux_low = lux_ok and all(v < self.config.light.lux_close for v in self.lux_buffer)

        sensor_open = (wo <= now < wc) and all_lux_high
        sensor_close = (now >= wc or now < wo) and all_lux_low
        abs_open = ao <= now < wc  # backstop fires inside sensor window
        abs_close = now >= ac or now < wo  # backstop fires in night zone

        if self.state == GateState.IDLE_CLOSED:
            if sensor_open or abs_open:
                self._trigger = MovementTrigger.AUTO
                self.state = GateState.MOVING_OPEN

        elif self.state == GateState.IDLE_OPEN:
            if sensor_close or abs_close:
                self._trigger = MovementTrigger.AUTO
                self.state = GateState.MOVING_CLOSE

        elif self.state == GateState.MANUAL_HOLD_OPEN:
            if abs_close:
                self._trigger = MovementTrigger.AUTO
                self.state = GateState.MOVING_CLOSE

        elif self.state == GateState.MANUAL_HOLD_CLOSED:
            if abs_open:
                self._trigger = MovementTrigger.AUTO
                self.state = GateState.MOVING_OPEN

        elif self.state == GateState.INIT:
            self._handle_init()

        # SAFETY_STOP / ERROR: no-op — only reboot or manual_move escapes these

    def _handle_init(self) -> None:
        top = self.limit_top.value() == 0
        bottom = self.limit_bottom.value() == 0

        if top and bottom:
            self._enter_error()
            return
        if top:
            self.state = GateState.IDLE_OPEN
            return
        if bottom:
            self.state = GateState.IDLE_CLOSED
            return
        # Both inactive: decide direction by time
        self._trigger = MovementTrigger.AUTO
        self.state = GateState.MOVING_OPEN if self.is_daytime() else GateState.MOVING_CLOSE

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def is_daytime(self) -> bool:
        from src.astro import local_minutes

        y, mo, d, h, minute, *_ = self.rtc.datetime()
        wo, wc, *_ = self._today_times
        now = local_minutes(y, mo, d, h, minute)
        return wo <= now < wc

    def manual_move(self, action: str) -> None:
        """Highest priority. Works from any non-ERROR state. Interrupts opposite movement."""
        if self.state == GateState.ERROR:
            return  # only reboot escapes ERROR

        if action == "open" and self.state in (
            GateState.IDLE_OPEN,
            GateState.MANUAL_HOLD_OPEN,
            GateState.MOVING_OPEN,
        ):
            return
        if action == "close" and self.state in (
            GateState.IDLE_CLOSED,
            GateState.MANUAL_HOLD_CLOSED,
            GateState.MOVING_CLOSE,
        ):
            return

        if self.state in (GateState.MOVING_OPEN, GateState.MOVING_CLOSE):
            self._abort_move = True  # _run_move detects this and clears it (Phase 4)
            self.motor.stop()
            self.nsleep.value(0)

        import contextlib

        is_recovery = self.state == GateState.SAFETY_STOP
        self._trigger = MovementTrigger.RECOVERY if is_recovery else MovementTrigger.MANUAL
        self.state = GateState.MOVING_OPEN if action == "open" else GateState.MOVING_CLOSE
        self._today_rec[3] += 1  # manual_count

        import asyncio

        with contextlib.suppress(RuntimeError):  # no running event loop in test context
            asyncio.get_running_loop().create_task(self._run_move(action))

    def simulate_timeout(self) -> None:
        """Test hook — moves _move_start_ms past timeout boundary."""
        from src.compat import ticks_ms

        self._move_start_ms = ticks_ms() - (self.config.safety.move_timeout_s * 1000 + 1)

    def get_forecast(self, days: int = 30) -> list[tuple]:
        """Return [(wo, wc, ao, ac), ...] for next N days. Used by SVG chart (Phase 5)."""
        from src.astro import is_dst, sun_times_cet
        from src.config import (
            abs_close_local,
            abs_open_local,
            window_close_local,
            window_open_local,
        )

        result = []
        y, mo, d, *_ = self.rtc.datetime()
        for _ in range(days):
            rise, sset = sun_times_cet(y, mo, d)
            dst = 60 if is_dst(y, mo, d, 12) else 0
            wo = window_open_local(self.config, rise, dst)
            wc = window_close_local(self.config, sset, dst)
            ao = abs_open_local(self.config, rise, dst)
            ac = abs_close_local(self.config, sset, dst)
            ao = max(ao, wo)
            ac = max(ac, wc)
            result.append((wo, wc, ao, ac))
            y, mo, d = _next_day(y, mo, d)
        return result

    def get_sun_forecast(self, days: int = 30) -> list[tuple]:
        """Return [(rise_local_min, set_local_min), ...] for next N days."""
        from src.astro import is_dst, sun_times_cet

        result = []
        y, mo, d, *_ = self.rtc.datetime()
        for _ in range(days):
            rise, sset = sun_times_cet(y, mo, d)
            dst = 60 if is_dst(y, mo, d, 12) else 0
            result.append((rise + dst, sset + dst))
            y, mo, d = _next_day(y, mo, d)
        return result

    def status_json(self) -> str:
        import json

        y, mo, d, h, minute, *_ = self.rtc.datetime()
        wo, wc, ao, ac = self._today_times
        sun = self.get_sun_forecast(1)
        rise_today, set_today = sun[0] if sun else (None, None)
        return json.dumps(
            {
                "state": self.state.value,
                "lux": self.lux_buffer,
                "time": f"{h:02d}:{minute:02d}",
                "limit_top": self.limit_top.value() == 0,
                "limit_bottom": self.limit_bottom.value() == 0,
                "vbat_v": None,  # Phase 8 wires real ADC
                "config_warning": self._config_warning,
                "today_schedule": {"wo": wo, "wc": wc, "ao": ao, "ac": ac},
                "sunrise_today": rise_today,
                "sunset_today": set_today,
            }
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_times(self, y: int, m: int, d: int, h: int, minute: int) -> None:
        """Compute (wo, wc, ao, ac) for today. Soft-clamp if invariant violated."""
        from src.astro import is_dst, sun_times_cet
        from src.config import (
            abs_close_local,
            abs_open_local,
            window_close_local,
            window_open_local,
        )

        rise, sset = sun_times_cet(y, m, d)
        dst = 60 if is_dst(y, m, d, h) else 0

        wo = window_open_local(self.config, rise, dst)
        wc = window_close_local(self.config, sset, dst)
        ao = abs_open_local(self.config, rise, dst)
        ac = abs_close_local(self.config, sset, dst)

        warnings: list[str] = []
        if ao < wo:
            warnings.append(
                f"abs_open {ao // 60:02d}:{ao % 60:02d} < "
                f"window_open {wo // 60:02d}:{wo % 60:02d} — clamped"
            )
            ao = wo
        if ac < wc:
            warnings.append(
                f"abs_close {ac // 60:02d}:{ac % 60:02d} < "
                f"window_close {wc // 60:02d}:{wc % 60:02d} — clamped"
            )
            ac = wc
        if ao >= ac:
            warnings.append("abs_open >= abs_close — backstop conflict, verify config")

        self._config_warning = "; ".join(warnings)
        if self._config_warning:
            self._log_warning(self._config_warning)
        self._today_times = (wo, wc, ao, ac)

    def _i2c_call(self, fn):
        try:
            result = fn()
            self._i2c_fail_count = 0
            return result
        except OSError:
            self._i2c_fail_count += 1
            if self._i2c_fail_count >= 3:
                self._enter_error()
            return None

    def _safety_stop(self) -> None:
        self.nsleep.value(0)  # nSLEEP=LOW (~2µA) — always
        self.motor.stop()  # IN1=IN2=LOW coast — best-effort via PCF8574
        if self.state != GateState.SAFETY_STOP:
            self._today_rec_error_count += 1
            self._today_rec[4] = self._today_rec_error_count
        self.state = GateState.SAFETY_STOP

    def _enter_error(self) -> None:
        self.motor.stop()
        self.nsleep.value(0)
        self.state = GateState.ERROR

    def _on_midnight(self, y: int, m: int, d: int, h: int, minute: int) -> None:
        from src.logs import days_since_2025, write_record

        try:
            write_record(tuple(self._today_rec))
        except Exception as e:
            self._log_warning(f"log write failed: {e}")
        self._today_rec = [days_since_2025(y, m, d), 0xFFFF, 0xFFFF, 0, 0]
        self._today_rec_error_count = 0
        self._resolve_times(y, m, d, h, minute)

    def _on_limit_reached(self) -> None:
        """Transition MOVING_* → IDLE_* or MANUAL_HOLD_* based on _trigger."""
        from src.astro import local_minutes

        y, mo, d, h, minute, *_ = self.rtc.datetime()
        now = local_minutes(y, mo, d, h, minute)
        opening = self.state == GateState.MOVING_OPEN

        if opening:
            if self._today_rec[1] == 0xFFFF:
                self._today_rec[1] = now
            self.state = (
                GateState.MANUAL_HOLD_OPEN
                if self._trigger == MovementTrigger.MANUAL
                else GateState.IDLE_OPEN
            )
        else:
            self._today_rec[2] = now
            self.state = (
                GateState.MANUAL_HOLD_CLOSED
                if self._trigger == MovementTrigger.MANUAL
                else GateState.IDLE_CLOSED
            )

    def _log_warning(self, msg: str) -> None:
        print(f"[WARNING] {msg}")  # Phase 6 writes to binary log

    async def _run_move(self, direction: str) -> None:
        from src.compat import sleep_ms, ticks_ms

        self._move_start_ms = ticks_ms()
        self.nsleep.value(1)
        await sleep_ms(1)

        if direction == "open":
            self.motor.forward()
            limit_pin = self.limit_top
        else:
            self.motor.backward()
            limit_pin = self.limit_bottom

        while limit_pin.value() == 1:  # HIGH = not triggered (pull-up)
            if self._abort_move:
                self._abort_move = False
                self.motor.stop()
                self.nsleep.value(0)
                return
            if self.nfault.value() == 0:  # active-low fault
                self._safety_stop()
                return
            if self.state == GateState.SAFETY_STOP:  # timeout detected by tick()
                return
            await sleep_ms(20)

        self.motor.stop()
        self.nsleep.value(0)
        self._on_limit_reached()
