# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
import pytest

from src.config import default_config
from src.state import CoopController, GateState
from tests.mock_hardware import (
    MockButton,
    MockLED,
    MockLightSensor,
    MockMotor,
    MockNFault,
    MockNSleep,
    MockPCF,
    MockRTC,
)


@pytest.fixture
def ctrl():
    config = default_config()
    # Legacy mode + fixed overrides → deterministic times independent of sun position
    config.window.mode = "legacy"
    config.window.legacy.hour_open = 6  # wo = 360 min local
    config.window.legacy.hour_close = 18  # wc = 1080 min local
    config.override_open.mode = "fixed"
    config.override_open.fixed_hour = 8  # ao = 480 min local
    config.override_close.mode = "fixed"
    config.override_close.fixed_hour = 22  # ac = 1320 min local

    c = CoopController(
        motor=MockMotor(),
        rtc=MockRTC(),
        light_sensor=MockLightSensor(),
        pcf=MockPCF(),
        limit_top=MockButton(active=False),
        limit_bottom=MockButton(active=False),
        btn_open=MockButton(),
        btn_close=MockButton(),
        leds=(MockLED(), MockLED(), MockLED()),  # red, yellow, green
        nsleep=MockNSleep(),
        nfault=MockNFault(),
        config=config,
    )
    # Standard start state for most tests
    c.state = GateState.IDLE_CLOSED
    c.lux_buffer = [10.0] * 5
    c.lux_ready = True
    return c
