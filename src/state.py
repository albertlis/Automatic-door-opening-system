# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
import asyncio
import json

from astro import local_minutes, next_day
from compat import sleep_ms, ticks_diff, ticks_ms
from config import schedule


class GateState:
    INIT = "INIT"
    IDLE_OPEN = "IDLE_OPEN"
    IDLE_CLOSED = "IDLE_CLOSED"
    MOVING_OPEN = "MOVING_OPEN"
    MOVING_CLOSE = "MOVING_CLOSE"
    MANUAL_HOLD_OPEN = "MANUAL_HOLD_OPEN"
    MANUAL_HOLD_CLOSED = "MANUAL_HOLD_CLOSED"
    SAFETY_STOP = "SAFETY_STOP"
    ERROR = "ERROR"


class MovementTrigger:
    AUTO = 0
    MANUAL = 1
    RECOVERY = 2


REVERSAL_DEAD_MS = 200  # coast before driving the opposite way (decision 11)


class CoopController:
    def __init__(self, motor, rtc, limit_top, limit_bottom, nsleep, nfault, config: dict):
        self.motor = motor
        self.rtc = rtc
        self.limit_top = limit_top  # active-low: 0 = at top
        self.limit_bottom = limit_bottom  # active-low: 0 = at bottom
        self.nsleep = nsleep  # DRV8833 nSLEEP
        self.nfault = nfault  # DRV8833 nFAULT, active-low
        self.config = config
        self.state = GateState.INIT
        self.today_schedule: tuple[int, int] | None = None  # (open_min, close_min) local, None if unsynced
        self.config_warning = ""
        self.rtc_synced: bool | None = None  # None = never read, True/False from last successful read (R31)
        self._date: tuple[int, int, int] | None = None  # local date today_schedule belongs to
        self._last_local: tuple[int, int, int, int] | None = None  # (y, m, d, local minute), None if unsynced
        self._trigger = MovementTrigger.AUTO
        self._move_start_ms = 0
        self._move_gen = 0  # bumped per _start_move / _halt; older _run_move tasks see mismatch and exit
        self._hold_desired: str | None = None  # desired() at manual_move time; hold expires when it changes
        self._i2c_fail_count = 0
        self._init_sample: tuple[bool, bool] | None = None  # INIT: last (top, bottom) read (R37)

    # Hooks: main.py assigns instance attributes. Defaults are no-ops (R4, R5, R29).
    def on_event(self, kind: str, trigger: int) -> None:
        pass

    def on_day_change(self, prev: tuple, new: tuple) -> None:
        pass

    def _emit(self, kind: str, trigger: int) -> None:
        try:
            self.on_event(kind, trigger)
        except Exception as e:
            print("on_event:", e)

    def _emit_day(self, prev: tuple, new: tuple) -> None:
        try:
            self.on_day_change(prev, new)
        except Exception as e:
            print("on_day_change:", e)

    # ------------------------------------------------------------------
    # Sync tick, every 2 s. No asyncio in here.
    # ------------------------------------------------------------------

    def tick(self) -> None:
        if self.state in (GateState.MOVING_OPEN, GateState.MOVING_CLOSE):
            if self._timed_out():  # backstop in case _run_move task died
                self._halt(GateState.SAFETY_STOP)
            return
        if self.state in (GateState.SAFETY_STOP, GateState.ERROR):
            self._read_clock()  # keeps rtc_synced, _last_local, day change current (R30); never moves
            return
        if self.state == GateState.INIT:
            top = self.limit_top.value() == 0
            bottom = self.limit_bottom.value() == 0
            sample = (top, bottom)
            if sample != self._init_sample:  # first read, or reads disagree: no decision yet (R37)
                self._init_sample = sample
                return
            if top and bottom:
                self._halt(GateState.ERROR)
                return
            if top:
                self.state = GateState.IDLE_OPEN
            elif bottom:
                self.state = GateState.IDLE_CLOSED
        clock = self._read_clock()
        if clock is None:
            return  # I2C failure (ERROR after 3rd in a row)
        synced, now = clock
        want = self.desired(now) if synced else None
        if self.state == GateState.INIT:  # neither limit: home toward desired; unknown -> close
            self._start_move("open" if want == "open" else "close", MovementTrigger.AUTO)
            return
        holding = self.state in (GateState.MANUAL_HOLD_OPEN, GateState.MANUAL_HOLD_CLOSED)
        if holding and want is not None and self._hold_desired == "?":  # press-time clock read failed: adopt now
            self._hold_desired = want
        if holding and want is not None and want != self._hold_desired:  # schedule moved on -> hold expires
            opening = self.state == GateState.MANUAL_HOLD_OPEN
            self.state = GateState.IDLE_OPEN if opening else GateState.IDLE_CLOSED
        if self.state == GateState.IDLE_CLOSED and want == "open":
            self._start_move("open", MovementTrigger.AUTO)
        elif self.state == GateState.IDLE_OPEN and want == "closed":
            self._start_move("close", MovementTrigger.AUTO)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enter_error(self) -> None:
        """Public ERROR entry for main.py (R12). Never _halt from main."""
        self._halt(GateState.ERROR)

    def manual_move(self, action: str, source: str) -> bool:
        """Button or web. True = move started or reversal accepted. Web ignores the bool (R1)."""
        if action not in ("open", "close") or source not in ("button", "web"):
            raise ValueError("manual_move: action open|close, source button|web")
        s = self.state
        if s == GateState.ERROR:
            return False
        if source == "web" and s == GateState.SAFETY_STOP:
            return False  # SAFETY_STOP cleared by physical button only (decision 1)
        if action == "open" and s in (GateState.IDLE_OPEN, GateState.MANUAL_HOLD_OPEN, GateState.MOVING_OPEN):
            return False
        if action == "close" and s in (GateState.IDLE_CLOSED, GateState.MANUAL_HOLD_CLOSED, GateState.MOVING_CLOSE):
            return False
        clock = self._read_clock()
        if self.state == GateState.ERROR:  # I2C may have errored us
            return False
        # "?" = I2C glitch at press time (unknown, adopt on next good read); None = unsynced/no-auto (R38)
        self._hold_desired = self.desired(clock[1]) if clock else "?"
        trigger = MovementTrigger.RECOVERY if s == GateState.SAFETY_STOP else MovementTrigger.MANUAL
        reverse = s in (GateState.MOVING_OPEN, GateState.MOVING_CLOSE)
        self._start_move("open" if action == "open" else "close", trigger, reverse)
        return True

    def desired(self, now: int) -> str | None:
        """'open' | 'closed' for local minute `now`. None if unsynced or open >= close today (no auto)."""
        if self.today_schedule is None:
            return None
        o, c = self.today_schedule
        if o >= c:
            return None
        return "open" if o <= now < c else "closed"

    def forecast(self, days: int = 30) -> list[tuple[int, int]]:
        r = self._i2c_call(self.rtc.datetime)
        if r is None or r[0] < 2020 or self._i2c_call(self.rtc.osf) is not False:
            return []  # unsynced, OSF=1 (time untrusted) or I2C failure
        y, mo, d = r[0], r[1], r[2]
        out = []
        for _ in range(days):
            out.append(schedule(self.config, y, mo, d))
            y, mo, d = next_day(y, mo, d)
        return out

    def status_json(self) -> str:
        """Cached values only, no RTC I/O (R32)."""
        ll = self._last_local
        sch = self.today_schedule
        return json.dumps(
            {
                "state": self.state,
                "time": None if ll is None else f"{ll[3] // 60:02d}:{ll[3] % 60:02d}",
                "limit_top": self.limit_top.value() == 0,
                "limit_bottom": self.limit_bottom.value() == 0,
                "config_warning": self.config_warning,
                "today_schedule": None if sch is None else {"open": sch[0], "close": sch[1]},
                "rtc_synced": self.rtc_synced is True,
            }
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _read_clock(self) -> tuple[bool, int] | None:
        """(synced, local_now_min); None on I2C failure. Updates rtc_synced, _last_local, schedule, day hook."""
        r = self._i2c_call(self.rtc.datetime)
        if r is None:
            return None
        osf = self._i2c_call(self.rtc.osf)
        if osf is None:
            return None
        y, mo, d, h, minute = r[:5]
        if y < 2020 or osf:  # unsynced, or OSF = time untrusted (R17): no schedule, no auto moves
            self.rtc_synced = False
            self._last_local = None
            self.today_schedule = None
            self._date = None
            return False, 0
        now = local_minutes(y, mo, d, h, minute)
        if now >= 1440:  # CEST: CET 23:xx is already next local day
            now -= 1440
            y, mo, d = next_day(y, mo, d)
        prev = None if self._last_local is None else self._last_local[:3]
        self.rtc_synced = True
        self._last_local = (y, mo, d, now)
        if (y, mo, d) != self._date:
            self._date = (y, mo, d)
            self.today_schedule = schedule(self.config, y, mo, d)
            o, c = self.today_schedule
            self.config_warning = "open >= close: no auto moves today" if o >= c else ""
        if prev is not None and prev != (y, mo, d):
            self._emit_day(prev, (y, mo, d))
        return True, now

    def _start_move(self, direction: str, trigger: int, reverse: bool = False) -> None:
        """Single owner of movement. Bumps _move_gen so any older _run_move task exits untouched."""
        self._trigger = trigger
        self.state = GateState.MOVING_OPEN if direction == "open" else GateState.MOVING_CLOSE
        self._move_start_ms = ticks_ms()
        self._move_gen += 1
        coro = self._run_move(direction, self._move_gen, reverse)
        try:
            asyncio.create_task(coro)
        except RuntimeError:  # no running loop (CPython tests)
            coro.close()

    def _stop_motor(self) -> None:
        """Stop order (R19): nSLEEP LOW first (GPIO kill switch), then IN1=IN2=LOW best effort."""
        self.nsleep.value(0)
        try:
            self.motor.stop()
        except OSError:
            pass  # driver already asleep, or PCF unreachable

    async def _run_move(self, direction: str, gen: int, reverse: bool = False) -> None:
        if gen != self._move_gen:
            return
        if reverse:  # coast, then drive the opposite way (decision 11)
            self._stop_motor()
            await sleep_ms(REVERSAL_DEAD_MS)
            if gen != self._move_gen:
                return
        self.nsleep.value(1)
        await sleep_ms(1)
        if gen != self._move_gen:
            return
        try:
            if direction == "open":
                self.motor.forward()
                limit = self.limit_top
            else:
                self.motor.backward()
                limit = self.limit_bottom
        except OSError:  # PCF8574 unreachable: cannot drive, do not leave nSLEEP high (R36)
            self._halt(GateState.ERROR)
            return
        while gen == self._move_gen and limit.value() == 1:  # 1 = not triggered (pull-up)
            if self.nfault.value() == 0 or self._timed_out():
                self._halt(GateState.SAFETY_STOP)
                return
            await sleep_ms(20)
        if gen != self._move_gen or self.state not in (GateState.MOVING_OPEN, GateState.MOVING_CLOSE):
            return  # superseded, or tick already safety-stopped us
        self._stop_motor()
        self._on_limit_reached()

    def _on_limit_reached(self) -> None:
        opening = self.state == GateState.MOVING_OPEN
        if self._trigger == MovementTrigger.MANUAL:
            self.state = GateState.MANUAL_HOLD_OPEN if opening else GateState.MANUAL_HOLD_CLOSED
        else:
            self.state = GateState.IDLE_OPEN if opening else GateState.IDLE_CLOSED
        self._emit("open" if opening else "close", self._trigger)

    def _timed_out(self) -> bool:
        return ticks_diff(ticks_ms(), self._move_start_ms) > self.config["safety"]["move_timeout_s"] * 1000

    def _halt(self, state: str) -> None:
        was = self.state
        self._move_gen += 1  # running _run_move must exit silently, never overwrite this state
        self.state = state
        self._stop_motor()
        if state == GateState.ERROR:
            if was != GateState.ERROR:  # ERROR-once guard (R29)
                self._emit("error", MovementTrigger.AUTO)
        elif state == GateState.SAFETY_STOP:
            self._emit("safety_stop", self._trigger)

    def _i2c_call(self, fn):
        try:
            result = fn()
            self._i2c_fail_count = 0
            return result
        except OSError:
            self._i2c_fail_count += 1
            if self._i2c_fail_count >= 3:
                self._halt(GateState.ERROR)
            return None
