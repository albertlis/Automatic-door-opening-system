# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
"""Dev runner: mock hardware + web UI on http://localhost:5000. CPython only, never uploaded to device."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, "src")  # flat imports in src/ (same as device)

import logs  # noqa: E402
from main import make_on_event_log  # noqa: E402
from tests.mock_hardware import MockPin, make_ctrl  # noqa: E402
from web import create_app  # noqa: E402


async def _tick_loop(ctrl):
    while True:
        ctrl.tick()
        await asyncio.sleep(2)


async def _door_sim(ctrl):
    # ponytail: time model, not position. Forward 3 s -> top hit; backward 3 s -> bottom hit.
    loop = asyncio.get_running_loop()
    top, bottom, motor = ctrl.limit_top, ctrl.limit_bottom, ctrl.motor
    moving, t0 = None, 0.0
    while True:
        await asyncio.sleep(0.1)
        if motor.state != moving:
            moving, t0 = motor.state, loop.time()
            if moving == "forward":
                bottom.value(1)  # leaving bottom
            elif moving == "backward":
                top.value(1)  # leaving top
        elif loop.time() - t0 >= 3:
            if moving == "forward":
                top.value(0)
            elif moving == "backward":
                bottom.value(0)


def _diag():  # fake /api/diag payload, same keys as main.make_diag_cb
    return {
        "rtc": True,
        "pcf": True,
        "osf": False,
        "top": 1,
        "bottom": 0,
        "nfault": 1,
        "b_open": 1,
        "b_close": 1,
        "mhz": 80,
        "mem": 117,
        "fs_kb": 800,
        "up_s": 42,
        "clients": None,
    }


async def main():
    logs.LOG_PATH = os.path.join(tempfile.gettempdir(), "coop_run_local_logs.bin")  # not repo root
    logs.init_log()
    ctrl = make_ctrl(limit_bottom=MockPin(0))  # start at bottom (limit hit) -> IDLE_CLOSED
    ctrl.on_event = make_on_event_log(ctrl)
    # ponytail: 127.0.0.1 only, no auth (API has none). Dev box, not the coop.
    app = create_app(
        ctrl,
        touch_cb=lambda: None,
        config_path="config.local.json",
        index_path="src/www/index.html",
        diag_cb=_diag,
    )
    tick = asyncio.create_task(_tick_loop(ctrl))
    sim = asyncio.create_task(_door_sim(ctrl))
    print("Running at http://localhost:5000")
    server = asyncio.create_task(app.start_server(host="127.0.0.1", port=5000))
    await server
    tick.cancel()
    sim.cancel()


if __name__ == "__main__":
    asyncio.run(main())
