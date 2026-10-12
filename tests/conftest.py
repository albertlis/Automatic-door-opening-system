# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
import pytest

import state
from state import GateState
from tests.mock_hardware import make_ctrl


@pytest.fixture
def ctrl(monkeypatch):
    # Fixed schedule: open 06:00, close 18:00 local (minutes 360 / 1080) -> deterministic tests
    monkeypatch.setattr(state, "schedule", lambda cfg, y, m, d: (360, 1080))
    c = make_ctrl()  # RTC 2026-06-15 12:00 CET, synced
    c.state = GateState.IDLE_CLOSED
    return c
