# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
"""Run web UI locally on CPython for development/testing."""
import asyncio

import src.main as main_module
from src.config import default_config
from src.state import CoopController, GateState
from src.web import create_app
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


def make_ctrl() -> CoopController:
    cfg = default_config()
    cfg.window.mode = "legacy"
    cfg.override_open.mode = "fixed"
    cfg.override_open.fixed_hour = 8
    cfg.override_close.mode = "fixed"
    cfg.override_close.fixed_hour = 22
    c = CoopController(
        motor=MockMotor(),
        rtc=MockRTC(),
        light_sensor=MockLightSensor(),
        pcf=MockPCF(),
        limit_top=MockButton(active=False),
        limit_bottom=MockButton(active=True),
        btn_open=MockButton(),
        btn_close=MockButton(),
        leds=(MockLED(), MockLED(), MockLED()),
        nsleep=MockNSleep(),
        nfault=MockNFault(),
        config=cfg,
    )
    c.state = GateState.IDLE_CLOSED
    c.lux_buffer = [5.0, 6.0, 7.0, 5.5, 6.5]
    c.lux_ready = True
    return c


async def run() -> None:
    ctrl = make_ctrl()
    main_module.ctrl = ctrl
    app = create_app(ctrl, config_path="config.json")
    print("Running at http://localhost:5000")
    await app.start_server(host="localhost", port=5000, debug=True)


if __name__ == "__main__":
    asyncio.run(run())
