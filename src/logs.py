# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
# Event log: 4 B binary records ">HH" = (day since 2025-01-01, (minute<<4)|(kind<<2)|trigger).
# RAM ring, flushed to LOG_PATH (append, capped with compaction). Pure int date math (no time module).
import os
import struct

from astro import civil_from_days, days_from_civil

LOG_PATH = "/logs.bin"
RING_SIZE = 64
MAX_RECORDS = 1536  # 6144 B
KEEP_RECORDS = 1152  # kept on compaction
MAX_DAY = 0xFFFE
DAY_ERASED = 0xFFFF
MAX_MINUTE = 1439


KIND_OPEN = 0
KIND_CLOSE = 1
KIND_SAFETY = 2
KIND_ERROR = 3
KIND_NAME = ("open", "close", "safety_stop", "error")
KIND_BY_NAME = {"open": KIND_OPEN, "close": KIND_CLOSE, "safety_stop": KIND_SAFETY, "error": KIND_ERROR}

# MovementTrigger values (state.py): 0=AUTO, 1=MANUAL, 2=RECOVERY. Plain ints, logs does not import state.
TRIGGER_NAME = ("auto", "manual", "recovery")

_replace = getattr(os, "replace", os.rename)
_ring = []  # (day, minute, kind, trigger), oldest first


_BASE = days_from_civil(2025, 1, 1)


def days_since_2025(y, m, d):
    return days_from_civil(y, m, d) - _BASE


def date_from_days(n):
    return civil_from_days(n + _BASE)


def pack_record(day, minute, kind, trigger):
    if not (0 <= day <= MAX_DAY and 0 <= minute <= MAX_MINUTE and 0 <= kind <= KIND_ERROR and 0 <= trigger <= 2):
        raise ValueError("log field out of range")
    return struct.pack(">HH", day, (minute << 4) | (kind << 2) | trigger)


def unpack_record(b):
    day, packed = struct.unpack(">HH", b)
    if day == DAY_ERASED or packed & 0x8000:
        return None
    minute = packed >> 4
    trigger = packed & 3
    if minute > MAX_MINUTE or trigger > 2:
        return None
    return day, minute, (packed >> 2) & 3, trigger


def _path(path):
    return LOG_PATH if path is None else path


def _size(p):
    try:
        return os.stat(p)[6]
    except OSError:
        return 0


def _exists(p):
    try:
        os.stat(p)
        return True
    except OSError:
        return False


def _read_span(p, start, n):
    with open(p, "rb") as f:
        f.seek(start * 4)
        return f.read(n * 4)


def _rewrite(p, data):
    tmp = p + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    _replace(tmp, p)


def _parse(raw):
    out = []
    for i in range(0, len(raw) - 3, 4):
        rec = unpack_record(raw[i : i + 4])
        if rec is not None:
            out.append(rec)
    return out


def _trim_torn(p):
    size = _size(p)
    if size % 4:
        _rewrite(p, _read_span(p, 0, size // 4))


def _append(p, data):
    _trim_torn(p)
    count = _size(p) // 4
    if count + len(data) // 4 <= MAX_RECORDS:
        with open(p, "ab") as f:
            f.write(data)
        return
    # ponytail: compaction reads the kept tail into RAM (<= 4.6 KB); fine on ESP32-C3.
    start = max(0, count - KEEP_RECORDS)
    _rewrite(p, _read_span(p, start, count - start) + data)


def init_log(path=None):
    p = _path(path)
    if _exists(p + ".tmp"):
        os.remove(p + ".tmp")
    if not _exists(p):
        with open(p, "wb"):
            pass
    _trim_torn(p)


def record(day, minute, kind, trigger):
    if day < 0:  # before 2025-01-01: not recorded (R24)
        return
    if isinstance(kind, str):
        kind = KIND_BY_NAME.get(kind, -1)
    pack_record(day, minute, kind, trigger)  # validate first: a bad value must not poison the ring
    _ring.append((day, minute, kind, trigger))
    if len(_ring) >= RING_SIZE:
        flush()


def flush(path=None):
    if not _ring:
        return True
    try:
        _append(_path(path), b"".join([pack_record(*r) for r in _ring]))
    except OSError:
        del _ring[:-RING_SIZE]  # R24: drop oldest, keep ring bounded, retry next flush
        return False
    del _ring[:]
    return True


def read_events(path=None):
    p = _path(path)
    n = _size(p) // 4
    return _parse(_read_span(p, 0, n)) if n else []


def read_tail(n, path=None):
    p = _path(path)
    count = _size(p) // 4
    n = min(n, count)
    if n <= 0:
        return []
    return _parse(_read_span(p, count - n, n))


def _fmt(rec):
    day, minute, kind, trig = rec
    y, m, d = date_from_days(day)
    return {
        "date": f"{y:04d}-{m:02d}-{d:02d}",
        "time": f"{minute // 60:02d}:{minute % 60:02d}",
        "kind": KIND_NAME[kind],
        "trigger": TRIGGER_NAME[trig],
    }


def log_json(limit):
    if limit <= 0:
        return []
    out = [_fmt(r) for r in reversed(_ring)][:limit]
    rest = limit - len(out)
    if rest > 0:
        out += [_fmt(r) for r in reversed(read_tail(rest))]
    return out
