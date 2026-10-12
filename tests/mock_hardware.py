# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
from config import default_config
from state import CoopController


class MockPin:
    """GPIO stand-in. value() reads, value(v) writes. Limit switches: 1 = not triggered (pull-up)."""

    def __init__(self, value: int = 1):
        self._v = value

    def value(self, v: int | None = None) -> int | None:
        if v is None:
            return self._v
        self._v = v
        return None


class MockMotor:
    def __init__(self):
        self.state = "stop"
        self.commands: list[str] = []

    def _cmd(self, s: str) -> None:
        self.state = s
        self.commands.append(s)

    def forward(self) -> None:
        self._cmd("forward")

    def backward(self) -> None:
        self._cmd("backward")

    def stop(self) -> None:
        self._cmd("stop")


class MockRTC:
    """DS3231 stand-in. datetime() -> 7-tuple (y,mo,d,h,mi,s,wd), CET. osf_flag = OSF bit (R17, R22)."""

    def __init__(self, dt: tuple = (2026, 6, 15, 12, 0, 0, 0), osf: bool = False):
        self._dt = dt
        self.osf_flag = osf

    def datetime(self) -> tuple:
        return self._dt

    def osf(self) -> bool:
        return self.osf_flag

    def set_datetime(self, dt: tuple) -> None:
        """Takes >= 6 fields; clears OSF (R17)."""
        d = tuple(dt)
        self._dt = d[:6] + (d[6] if len(d) > 6 else 0,)
        self.osf_flag = False


class FakeMachine:
    """Stands in for the `machine` module: lightsleep, freq, reset. Records calls."""

    def __init__(self, freq_hz: int = 80_000_000):
        self._freq = freq_hz
        self.calls: list[tuple] = []

    def lightsleep(self, ms: int) -> None:
        self.calls.append(("lightsleep", ms))

    def freq(self, hz: int | None = None) -> int | None:
        if hz is None:
            return self._freq
        self.calls.append(("freq", hz))
        self._freq = hz
        return None

    def reset(self) -> None:
        self.calls.append(("reset",))


class FakeWdt:
    """machine.WDT stand-in: counts feed() calls."""

    def __init__(self):
        self.feeds = 0

    def feed(self) -> None:
        self.feeds += 1


def make_ctrl(config: dict | None = None, **pins) -> CoopController:
    """CoopController on mocks. Limits open (1), nSLEEP low, nFAULT ok. Override any kwarg via **pins."""
    kwargs = {
        "motor": MockMotor(),
        "rtc": MockRTC(),
        "limit_top": MockPin(1),
        "limit_bottom": MockPin(1),
        "nsleep": MockPin(0),
        "nfault": MockPin(1),
    }
    kwargs.update(pins)
    return CoopController(config=config if config is not None else default_config(), **kwargs)
