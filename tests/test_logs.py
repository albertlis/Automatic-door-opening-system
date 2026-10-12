"""Tests for src/logs.py (phase 6): 4 B binary records, RAM ring, daily flush.

Web (/api/log via microdot) and state-hook tests (06 items 29-31, 33-42) live elsewhere.
"""

import builtins
import os
import struct

import pytest

import logs

# MovementTrigger values (state.py), plain ints like logs.py
AUTO, MANUAL, RECOVERY = 0, 1, 2


@pytest.fixture(autouse=True)
def log_path(tmp_path, monkeypatch):
    path = str(tmp_path / "logs.bin")
    monkeypatch.setattr(logs, "LOG_PATH", path)
    logs._ring.clear()
    yield path
    logs._ring.clear()


def _write(path, data):
    with open(path, "wb") as f:
        f.write(data)


def _raw(path):
    with open(path, "rb") as f:
        return f.read()


def _overflow():
    """Record MAX_RECORDS + 100 events (day = index), flush the ring tail."""
    for i in range(logs.MAX_RECORDS + 100):
        logs.record(i, 0, logs.KIND_OPEN, AUTO)
    logs.flush()


# ---- 1-6 pure codec ----


def test_record_is_4_bytes():
    assert len(logs.pack_record(0, 0, logs.KIND_OPEN, AUTO)) == 4


@pytest.mark.parametrize("minute", [0, 1, 1439])
@pytest.mark.parametrize("kind", range(4))
@pytest.mark.parametrize("trigger", range(3))
def test_pack_unpack_roundtrip(minute, kind, trigger):
    rec = (1234, minute, kind, trigger)
    assert logs.unpack_record(logs.pack_record(*rec)) == rec


@pytest.mark.parametrize("minute,kind,trigger", [(1440, 0, 0), (0, 4, 0), (0, 0, 3)])
def test_pack_rejects_out_of_range(minute, kind, trigger):
    with pytest.raises(ValueError):
        logs.pack_record(0, minute, kind, trigger)


def test_erased_bytes_invalid():
    assert logs.unpack_record(b"\xff\xff\xff\xff") is None


def test_packed_bit15_reserved():
    # otherwise valid fields (minute 100, kind 1, trigger 1) plus bit 15
    assert logs.unpack_record(struct.pack(">HH", 5, 0x8000 | (100 << 4) | (1 << 2) | 1)) is None


def test_trigger_codes_match_state():
    import state

    states = (state.MovementTrigger.AUTO, state.MovementTrigger.MANUAL, state.MovementTrigger.RECOVERY)
    assert states == (AUTO, MANUAL, RECOVERY)
    assert logs.TRIGGER_NAME == ("auto", "manual", "recovery")


# ---- 7-10 date helpers ----


def test_days_base():
    assert logs.days_since_2025(2025, 1, 1) == 0
    assert logs.date_from_days(0) == (2025, 1, 1)


def test_days_year_boundary():
    assert logs.days_since_2025(2026, 1, 1) == 365


def test_days_leap_day():
    assert logs.days_since_2025(2028, 3, 1) == 1155


def test_date_roundtrip_2025_2035():
    for n in range(0, 3900):
        assert logs.days_since_2025(*logs.date_from_days(n)) == n


# ---- 11-18 file behaviour ----


def test_init_creates_empty_file(log_path):
    logs.init_log()
    assert os.path.getsize(log_path) == 0


def test_init_does_not_reset_existing(log_path):
    logs.record(0, 10, logs.KIND_OPEN, AUTO)
    logs.record(0, 20, logs.KIND_CLOSE, AUTO)
    logs.flush()
    logs.init_log()
    assert len(logs.read_events()) == 2


def test_append_and_read_order():
    recs = [(0, 10, 0, 0), (0, 20, 1, 0), (1, 5, 2, 1)]
    for r in recs:
        logs.record(*r)
    logs.flush()
    assert logs.read_events() == recs


def test_read_missing_file_returns_empty(tmp_path):
    assert logs.read_events(str(tmp_path / "nope.bin")) == []


def test_torn_tail_ignored_on_read(log_path):
    _write(log_path, logs.pack_record(0, 1, 0, 0) + logs.pack_record(0, 2, 1, 0) + b"\x01\x02")
    assert logs.read_events() == [(0, 1, 0, 0), (0, 2, 1, 0)]


def test_init_truncates_torn_tail(log_path):
    _write(log_path, logs.pack_record(0, 1, 0, 0) + logs.pack_record(0, 2, 1, 0) + b"\x01\x02")
    logs.init_log()
    assert os.path.getsize(log_path) == 8
    assert logs.read_events() == [(0, 1, 0, 0), (0, 2, 1, 0)]


def test_erased_record_skipped(log_path):
    _write(log_path, logs.pack_record(0, 1, 0, 0) + b"\xff\xff\xff\xff" + logs.pack_record(0, 2, 1, 0))
    assert logs.read_events() == [(0, 1, 0, 0), (0, 2, 1, 0)]


def test_leftover_tmp_removed_by_init(log_path):
    tmp = log_path + ".tmp"
    _write(tmp, b"junk")
    logs.init_log()
    assert not os.path.exists(tmp)


# ---- 19-23 ring + flush ----


def test_record_buffers_in_ram(log_path):
    logs.init_log()
    for i in range(3):
        logs.record(0, i, 0, 0)
    assert os.path.getsize(log_path) == 0
    assert len(logs._ring) == 3


def test_flush_writes_and_clears_ring(log_path):
    for i in range(3):
        logs.record(0, i, 0, 0)
    logs.flush()
    assert len(logs.read_events()) == 3
    assert len(logs._ring) == 0


def test_flush_noop_when_empty(log_path):
    logs.record(0, 1, 0, 0)
    logs.flush()
    before = _raw(log_path)
    logs.flush()
    assert _raw(log_path) == before


def test_ring_full_triggers_flush(log_path):
    for i in range(logs.RING_SIZE):
        logs.record(0, i, 0, 0)
    assert len(logs.read_events()) == logs.RING_SIZE
    assert len(logs._ring) == 0


def test_ring_never_exceeds_size():
    for i in range(200):
        logs.record(i, 0, 0, 0)
        assert len(logs._ring) <= logs.RING_SIZE


# ---- 24-26 cap / compaction ----


def test_cap_never_exceeds_max_bytes(log_path):
    _overflow()
    size = os.path.getsize(log_path)
    assert size <= logs.MAX_RECORDS * 4
    assert size < (logs.MAX_RECORDS + 100) * 4  # compaction actually happened


def test_compaction_keeps_newest_in_order(log_path):
    # Compaction fires on the 25th flush (1536 + 64 > MAX): keeps newest 1152 (days 384..1535),
    # appends 1536..1599; ring tail 1600..1635 flushed last.
    _overflow()
    days = [r[0] for r in logs.read_events()]
    assert days == list(range(384, logs.MAX_RECORDS + 100))


def test_compaction_leaves_no_tmp(log_path):
    _overflow()
    assert not os.path.exists(log_path + ".tmp")


# ---- 27-28, 32 read API (logic level) ----


def test_log_json_newest_first_limit(log_path):
    logs.record(0, 390, logs.KIND_OPEN, AUTO)  # 2025-01-01 06:30
    logs.record(0, 1085, logs.KIND_CLOSE, AUTO)  # 2025-01-01 18:05
    logs.record(1, 390, logs.KIND_OPEN, MANUAL)  # 2025-01-02 06:30
    logs.record(365, 0, logs.KIND_SAFETY, RECOVERY)  # 2026-01-01 00:00
    logs.record(365, 600, logs.KIND_ERROR, AUTO)  # 2026-01-01 10:00
    logs.flush()
    assert logs.log_json(3) == [
        {"date": "2026-01-01", "time": "10:00", "kind": "error", "trigger": "auto"},
        {"date": "2026-01-01", "time": "00:00", "kind": "safety_stop", "trigger": "recovery"},
        {"date": "2025-01-02", "time": "06:30", "kind": "open", "trigger": "manual"},
    ]


def test_log_json_includes_ram(log_path):
    logs.record(0, 390, logs.KIND_OPEN, AUTO)
    logs.flush()
    logs.record(1, 1085, logs.KIND_CLOSE, MANUAL)  # unflushed
    out = logs.log_json(5)
    assert out == [
        {"date": "2025-01-02", "time": "18:05", "kind": "close", "trigger": "manual"},
        {"date": "2025-01-01", "time": "06:30", "kind": "open", "trigger": "auto"},
    ]


def test_log_json_missing_file_returns_empty(log_path):
    assert logs.log_json(50) == []


def test_read_tail_newest_only(log_path):
    for i in range(100):
        logs.record(0, i, 0, 0)
    logs.flush()
    assert logs.read_tail(5) == [(0, i, 0, 0) for i in range(95, 100)]


# ---- 43-44 failure paths ----


def test_flush_oserror_caps_ring(monkeypatch):
    real_open = builtins.open

    def boom(*args, **kwargs):
        raise OSError("flash write failed")

    monkeypatch.setattr(builtins, "open", boom)
    for i in range(200):
        logs.record(i, 0, 0, 0)  # must not raise
        assert len(logs._ring) <= logs.RING_SIZE
    assert logs._ring[-1][0] == 199
    monkeypatch.setattr(builtins, "open", real_open)  # not monkeypatch.undo(): would also revert LOG_PATH
    assert logs.flush() is True
    assert len(logs.read_events()) == logs.RING_SIZE
    assert len(logs._ring) == 0


def test_record_skips_negative_day():
    logs.record(-1, 0, 0, 0)
    assert len(logs._ring) == 0


def test_record_accepts_state_kind_name():
    logs.record(0, 390, "safety_stop", AUTO)
    assert logs._ring == [(0, 390, logs.KIND_SAFETY, AUTO)]
