# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
class MockMotor:
    def __init__(self):
        self.commands: list[str] = []

    def forward(self) -> None:
        self.commands.append("forward")

    def backward(self) -> None:
        self.commands.append("backward")

    def stop(self) -> None:
        self.commands.append("stop")


class MockRTC:
    def __init__(self):
        # Default: synced, daytime, summer (CEST active) — 08:00 CET = 09:00 CEST local
        self._dt = (2024, 6, 1, 8, 0, 0, 0, 0)  # y,mo,d,h,min,sec,weekday,yearday

    def datetime(self) -> tuple:
        return self._dt

    def set_datetime(self, dt: tuple) -> None:
        self._dt = dt


class MockLightSensor:
    def __init__(self):
        self._lux: float = 10.0

    def read_lux(self) -> float:
        return self._lux

    def set_lux(self, v: float) -> None:
        self._lux = v


class MockButton:
    """Used for both limit switches and push buttons. value() mimics GPIO INPUT_PULLUP."""

    def __init__(self, active: bool = False):
        self._active = active  # True = pressed/triggered

    def value(self) -> int:
        return 0 if self._active else 1  # pull-up: 0=active, 1=inactive

    def activate(self) -> None:
        self._active = True

    def deactivate(self) -> None:
        self._active = False


class MockLED:
    def __init__(self):
        self.state: str = "off"
        self.blinking: bool = False

    def on(self) -> None:
        self.state = "on"
        self.blinking = False

    def off(self) -> None:
        self.state = "off"
        self.blinking = False

    def blink(self) -> None:
        self.blinking = True


class MockPCF:
    """Mock PCF8574 GPIO expander."""

    def __init__(self):
        self._pins: dict[int, int] = {}

    def set_pin(self, pin: int, val: int) -> None:
        self._pins[pin] = val

    def get_pin(self, pin: int) -> bool:
        return bool(self._pins.get(pin, 1))

    def read_all(self) -> int:
        result = 0xFF
        for pin, val in self._pins.items():
            if not val:
                result &= ~(1 << pin)
        return result


class MockNSleep:
    """Mock DRV8833 nSLEEP pin (GPIO21)."""

    def __init__(self):
        self._val: int = 0  # start in sleep

    def value(self, v: int | None = None) -> int:
        if v is not None:
            self._val = v
        return self._val


class MockNFault:
    """Mock DRV8833 nFAULT pin (GPIO20). active-low."""

    def __init__(self):
        self._val: int = 1  # HIGH = no fault

    def value(self) -> int:
        return self._val

    def trigger_fault(self) -> None:
        self._val = 0  # LOW = fault active
