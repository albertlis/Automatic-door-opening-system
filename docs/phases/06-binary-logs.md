# Phase 6 — Binary Logs

## Goal

Implement the persistent event log: binary circular buffer on LittleFS (or local filesystem in tests), daily record tracking, and `/api/logs` endpoint. Add `logs.html`.

## Prerequisites

- Phase 3 complete (state machine, daily record fields in `CoopController`)
- Phase 5 complete (`src/web.py`, stub `/api/logs` endpoint)

## Deliverables

| File | Status |
|------|--------|
| `src/logs.py` | create |
| `src/state.py` | extend (wire log events) |
| `src/web.py` | extend (real `/api/logs`) |
| `src/www/logs.html` | create |
| `tests/test_logs.py` | create |

## TDD Protocol

**Write `tests/test_logs.py` first. All tests must fail. Then implement `src/logs.py` and extensions until all pass.**

---

## 1. `src/logs.py`

Binary circular buffer, 365 records max, 8 bytes each.

### Format

```
File layout:
  Offset 0   : 4 bytes — total_written (uint32, big-endian)
  Offset 4   : N × 8 bytes — records (circular buffer)

Record (8 bytes, RECORD_FMT = '>HHHBB'):
  days_since_2025 : uint16  — (date - 2025-01-01).days, max ~179 years
  open_min        : uint16  — minutes from midnight; 0xFFFF = no open today
  close_min       : uint16  — minutes from midnight; 0xFFFF = no close today
  manual_count    : uint8   — manual interventions today
  error_count     : uint8   — safety_stop incidents today
```

### Constants

```python
import struct

LOG_PATH    = "/logs.bin"
MAX_RECORDS = 365
HEADER_SIZE = 4
RECORD_FMT  = ">HHHBB"
RECORD_SIZE = struct.calcsize(RECORD_FMT)  # must be 8
assert RECORD_SIZE == 8
```

### `init_log(path: str = LOG_PATH) → None`

```python
def init_log(path: str = LOG_PATH) -> None:
    """Create log file with 4-byte header if it doesn't exist."""
    try:
        open(path, "rb").close()
    except OSError:
        with open(path, "wb") as f:
            f.write(struct.pack(">I", 0))   # total_written = 0
```

### `write_record(rec: tuple, path: str = LOG_PATH) → None`

```python
def write_record(rec: tuple, path: str = LOG_PATH) -> None:
    """Append record to circular buffer. rec = (days_since_2025, open_min, close_min, manual_count, error_count)."""
    with open(path, "r+b") as f:
        total = struct.unpack(">I", f.read(4))[0]
        idx = total % MAX_RECORDS
        f.seek(HEADER_SIZE + idx * RECORD_SIZE)
        f.write(struct.pack(RECORD_FMT, *rec))
        f.seek(0)
        f.write(struct.pack(">I", total + 1))
```

### `read_records(path: str = LOG_PATH) → list[tuple]`

```python
def read_records(path: str = LOG_PATH) -> list[tuple]:
    """Read all records in chronological order (oldest first)."""
    with open(path, "rb") as f:
        total = struct.unpack(">I", f.read(4))[0]
        count = min(total, MAX_RECORDS)
        if count == 0:
            return []
        # If buffer wrapped: start at write_pos (oldest record)
        start = total % MAX_RECORDS if total >= MAX_RECORDS else 0
        records = []
        for i in range(count):
            idx = (start + i) % MAX_RECORDS
            f.seek(HEADER_SIZE + idx * RECORD_SIZE)
            records.append(struct.unpack(RECORD_FMT, f.read(RECORD_SIZE)))
    return records
```

### `days_since_2025(y: int, m: int, d: int) → int`

```python
def days_since_2025(y: int, m: int, d: int) -> int:
    """Convert date to integer days since 2025-01-01."""
    import time
    epoch_target = time.mktime((y, m, d, 0, 0, 0, 0, 0, -1))
    epoch_base   = time.mktime((2025, 1, 1, 0, 0, 0, 0, 0, -1))
    return int((epoch_target - epoch_base) / 86400)
```

### `records_to_json(records: list[tuple]) → list[dict]`

```python
def records_to_json(records: list[tuple]) -> list[dict]:
    """Convert raw tuples to dicts for JSON serialization."""
    result = []
    for rec in records:
        days, open_min, close_min, manual_count, error_count = rec
        result.append({
            "days_since_2025": days,
            "open_min": open_min if open_min != 0xFFFF else None,
            "close_min": close_min if close_min != 0xFFFF else None,
            "manual_count": manual_count,
            "error_count": error_count,
        })
    return result
```

---

## 2. Extend `src/state.py`

### Wire `_on_midnight` to write log

```python
def _on_midnight(self, y: int, m: int, d: int, h: int, minute: int) -> None:
    from src.logs import write_record  # lazy import — not available until Phase 6
    # Write yesterday's record before resetting
    try:
        write_record(tuple(self._today_rec))
    except Exception as e:
        self._log_warning(f"log write failed: {e}")
    # Reset for new day
    self._today_rec = [
        0,       # days_since_2025 — set at first event of new day
        0xFFFF,  # open_min
        0xFFFF,  # close_min
        0,       # manual_count
        0,       # error_count
    ]
    self._today_rec_error_count = 0
    self._resolve_times(y, m, d, h, minute)
```

### Update `_on_limit_reached` to set `days_since_2025` on first event of day

When recording the first open/close of the day, also set `_today_rec[0]`:

```python
# Inside _on_limit_reached, when setting open_min or close_min:
if self._today_rec[0] == 0:   # not yet set today
    from src.logs import days_since_2025
    self._today_rec[0] = days_since_2025(y, mo, d)
```

---

## 3. Extend `src/web.py` — Real `/api/logs`

Replace stub with:

```python
@app.route("/api/logs")
async def api_logs(request):
    from src.logs import read_records, records_to_json, LOG_PATH
    import json
    try:
        records = read_records(LOG_PATH)
        return Response(
            body=json.dumps(records_to_json(records)),
            headers={"Content-Type": "application/json"},
        )
    except OSError:
        return Response(body="[]", headers={"Content-Type": "application/json"})
```

---

## 4. `src/www/logs.html`

Simple table fetched via HTMX. No complex JS.

```html
<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Coop — Logs</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
  <script src="https://unpkg.com/htmx.org@2.0.0"></script>
</head>
<body>
  <main class="container">
    <h1>Dziennik Zdarzeń</h1>
    <nav>
      <a href="/">Status</a> |
      <a href="/config">Konfiguracja</a> |
      <a href="/logs">Logi</a> |
      <a href="/debug">Debug</a>
    </nav>

    <div hx-get="/api/logs" hx-trigger="load" hx-swap="innerHTML">
      <p aria-busy="true">Ładowanie…</p>
    </div>

    <script>
      // Transform JSON from /api/logs into a table
      document.body.addEventListener("htmx:afterOnLoad", function(evt) {
        if (evt.detail.pathInfo.requestPath !== "/api/logs") return;
        try {
          const records = JSON.parse(evt.detail.xhr.responseText);
          const table = buildTable(records);
          evt.detail.target.innerHTML = table;
        } catch (e) {}
      });

      function buildTable(records) {
        if (!records.length) return "<p>Brak zapisów.</p>";
        const rows = records.map(r => {
          const open  = r.open_min  !== null ? `${Math.floor(r.open_min/60).toString().padStart(2,"0")}:${(r.open_min%60).toString().padStart(2,"0")}` : "—";
          const close = r.close_min !== null ? `${Math.floor(r.close_min/60).toString().padStart(2,"0")}:${(r.close_min%60).toString().padStart(2,"0")}` : "—";
          return `<tr><td>${r.days_since_2025}</td><td>${open}</td><td>${close}</td><td>${r.manual_count}</td><td>${r.error_count}</td></tr>`;
        }).join("");
        return `<table><thead><tr><th>Dzień</th><th>Otwarcie</th><th>Zamknięcie</th><th>Manualne</th><th>Błędy</th></tr></thead><tbody>${rows}</tbody></table>`;
      }
    </script>
  </main>
</body>
</html>
```

---

## 5. `tests/test_logs.py`

```python
import struct
import os
import pytest
from src.logs import (
    init_log, write_record, read_records, records_to_json,
    days_since_2025, LOG_PATH, MAX_RECORDS, RECORD_SIZE, HEADER_SIZE,
)


@pytest.fixture
def log_path(tmp_path):
    return str(tmp_path / "test_logs.bin")


# --- init_log ---

def test_init_creates_file(log_path):
    init_log(log_path)
    assert os.path.exists(log_path)
    size = os.path.getsize(log_path)
    assert size == HEADER_SIZE

def test_init_file_total_written_zero(log_path):
    init_log(log_path)
    with open(log_path, "rb") as f:
        total = struct.unpack(">I", f.read(4))[0]
    assert total == 0

def test_init_is_noop_if_exists(log_path):
    init_log(log_path)
    # Write something manually
    with open(log_path, "r+b") as f:
        f.seek(0)
        f.write(struct.pack(">I", 42))
    init_log(log_path)  # second call must NOT reset total
    with open(log_path, "rb") as f:
        total = struct.unpack(">I", f.read(4))[0]
    assert total == 42


# --- write + read round-trip ---

def test_write_and_read_single_record(log_path):
    init_log(log_path)
    rec = (100, 360, 1080, 0, 0)   # day 100, open 06:00, close 18:00
    write_record(rec, log_path)
    records = read_records(log_path)
    assert len(records) == 1
    assert records[0] == rec

def test_write_multiple_records_chronological(log_path):
    init_log(log_path)
    recs = [(i, 360 + i, 1080 + i, 0, 0) for i in range(5)]
    for rec in recs:
        write_record(rec, log_path)
    result = read_records(log_path)
    assert result == recs

def test_read_empty_log(log_path):
    init_log(log_path)
    assert read_records(log_path) == []


# --- Circular buffer ---

def test_circular_buffer_wraps_at_max(log_path):
    init_log(log_path)
    # Write MAX_RECORDS + 1 records
    for i in range(MAX_RECORDS + 1):
        write_record((i, 360, 1080, 0, 0), log_path)
    records = read_records(log_path)
    assert len(records) == MAX_RECORDS
    # Oldest record should be record #1 (record #0 was overwritten)
    assert records[0][0] == 1

def test_circular_buffer_total_written_continues(log_path):
    init_log(log_path)
    for i in range(MAX_RECORDS + 5):
        write_record((i, 360, 1080, 0, 0), log_path)
    with open(log_path, "rb") as f:
        total = struct.unpack(">I", f.read(4))[0]
    assert total == MAX_RECORDS + 5

def test_circular_buffer_correct_order_after_wrap(log_path):
    """After wrap, records returned oldest-first (correct chronological order)."""
    init_log(log_path)
    n = MAX_RECORDS + 3
    for i in range(n):
        write_record((i, i, i, 0, 0), log_path)
    records = read_records(log_path)
    # Should be records 3..n-1 in order
    days = [r[0] for r in records]
    assert days == list(range(3, n))


# --- Sentinel values ---

def test_sentinel_no_open(log_path):
    init_log(log_path)
    rec = (100, 0xFFFF, 0xFFFF, 0, 0)
    write_record(rec, log_path)
    result = read_records(log_path)
    assert result[0][1] == 0xFFFF
    assert result[0][2] == 0xFFFF

def test_records_to_json_sentinel_becomes_none(log_path):
    init_log(log_path)
    write_record((100, 0xFFFF, 1080, 2, 1), log_path)
    records = read_records(log_path)
    js = records_to_json(records)
    assert js[0]["open_min"] is None
    assert js[0]["close_min"] == 1080
    assert js[0]["manual_count"] == 2
    assert js[0]["error_count"] == 1


# --- days_since_2025 ---

def test_days_since_2025_base():
    assert days_since_2025(2025, 1, 1) == 0

def test_days_since_2025_one_year():
    assert days_since_2025(2026, 1, 1) == 365  # 2025 is not leap

def test_days_since_2025_leap_year():
    # 2024 is leap, but base is 2025 so 2024 gives negative (before base)
    result = days_since_2025(2024, 12, 31)
    assert result == -1  # one day before 2025-01-01


# --- /api/logs endpoint ---

@pytest.mark.asyncio
async def test_api_logs_real_data(client, tmp_path, monkeypatch):
    """After writing records, /api/logs returns them."""
    import src.logs as logs_module
    log_path = str(tmp_path / "logs.bin")
    monkeypatch.setattr(logs_module, "LOG_PATH", log_path)
    init_log(log_path)
    write_record((100, 360, 1080, 0, 0), log_path)
    res = await client.get("/api/logs")
    data = res.json
    assert len(data) == 1
    assert data[0]["days_since_2025"] == 100

@pytest.mark.asyncio
async def test_api_logs_missing_file_returns_empty(client, monkeypatch):
    """If log file doesn't exist, return []."""
    import src.logs as logs_module
    monkeypatch.setattr(logs_module, "LOG_PATH", "/nonexistent/path.bin")
    res = await client.get("/api/logs")
    assert res.json == []


# --- File size correctness ---

def test_file_size_after_n_records(log_path):
    init_log(log_path)
    n = 10
    for i in range(n):
        write_record((i, 360, 1080, 0, 0), log_path)
    expected = HEADER_SIZE + n * RECORD_SIZE
    assert os.path.getsize(log_path) == expected

def test_file_size_does_not_grow_after_wrap(log_path):
    init_log(log_path)
    for i in range(MAX_RECORDS + 10):
        write_record((i, 360, 1080, 0, 0), log_path)
    expected = HEADER_SIZE + MAX_RECORDS * RECORD_SIZE
    assert os.path.getsize(log_path) == expected
```

---

## Acceptance Criteria

```bash
uv run pytest tests/test_logs.py -v   # all green
uv run pytest tests/test_api.py -v    # still all green (api/logs now real)
uv run ruff check src/logs.py tests/test_logs.py
```

## Notes

- `src/logs.py` uses `open()` with `"r+b"` mode for updates — this requires the file to exist. `init_log()` must always be called before `write_record()`. `boot.py` (Phase 8) calls `init_log()` at startup.
- `days_since_2025()` uses `time.mktime()` — available on CPython. MicroPython has `utime.mktime()` with same signature. No compat shim needed; `import time; time.mktime()` works on both.
- The `test_logs.py` fixture injects a temp path; production code uses `LOG_PATH = "/logs.bin"` (LittleFS root). Tests that call `client.get("/api/logs")` need `monkeypatch` to redirect to temp path.
- `_today_rec[0]` (days_since_2025) is set lazily on first event of the day. If midnight fires before any event (no opens/closes that day), the record has `days_since_2025=0`. Accept this edge case — it's a rare no-activity day.
- MicroPython `"r+b"` mode support: verify at flash time. If unavailable, use `read → modify in memory → write full file`. Keep this as a note in `hardware.py` (Phase 8).
