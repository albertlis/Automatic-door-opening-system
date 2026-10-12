# Phase 6 — Event Log (RAM ring + daily flush)

## Goal

Record door events (open, close, safety_stop, error) with local time and trigger.
Keep recent events in a RAM ring. Flush the ring to flash once per local day (at
midnight) and early when the ring is full. Expose `GET /api/log`.

No counters, no light data, no per-day summary records. Each event is one 4-byte record.
Format stays binary (R24): owner reads the log once in years, compactness wins.

## Prerequisites

- Phase 3 `src/state.py`: `tick()`, `_read_clock()`, `_on_limit_reached()`, `_halt()`, `MovementTrigger` (AUTO=0, MANUAL=1, RECOVERY=2).
- Phase 4 `src/state.py`: `manual_move(action, source)` returns bool (R1); `on_event` / `on_day_change` callbacks (R4, R5).
- Phase 5 `src/web.py` (microdot routes).
- Phase 8 `src/boot.py` calls `logs.init_log()`; `main.py` binds the callbacks (owner 04 §11: on_event / on_day_flush).

## Deliverables

| File | Action |
|------|--------|
| `src/logs.py` | create (replaces the old 8-byte counter design) |
| `src/state.py` | extend: 3 event hook points, `on_event` / `on_day_change` callbacks, `_last_local` cache. Never imports `logs` |
| `src/web.py` | add `GET /api/log`; `/api/status` gets key `events` (last 5) |
| `tests/test_logs.py` | rewrite (failing first) |
| `src/www/logs.html` | DROPPED. Log view merged into single `index.html` (decision 4) |

No ctor change in `CoopController`. `state.py` only calls callbacks; `main.py` binds them to `logs.py` functions.

---

## Design

### 1. Event model

| Field | Values | Code |
|-------|--------|------|
| kind | open, close, safety_stop, error | `KIND_OPEN=0, KIND_CLOSE=1, KIND_SAFETY=2, KIND_ERROR=3`; `KIND_BY_NAME = {"open":0, "close":1, "safety_stop":2, "error":3}` (state passes the string) |
| trigger | auto, manual, recovery | `TRIG_AUTO=0, TRIG_MANUAL=1, TRIG_RECOVERY=2` (= `state.MovementTrigger`, passed as int) |
| minute | local minute of day, 0..1439 | `_last_local[3]` |
| day | days since 2025-01-01, 0..65534 | `days_since_2025(_last_local[0..2])`. Negative (before 2025) = not recorded (R24) |

### 2. Record layout — 4 bytes, big-endian, `struct` format `">HH"`

| Bytes | Field | Encoding |
|-------|-------|----------|
| 0–1 | `day` | uint16, days since 2025-01-01. `0xFFFF` = erased/invalid |
| 2–3 | `packed` | uint16: bits 0–1 trigger, bits 2–3 kind, bits 4–14 minute, bit 15 = 0 (reserved, must be 0) |

```
packed = (minute << 4) | (kind << 2) | trigger
```

- Max packed value = `(1439 << 4) | (3 << 2) | 2` = 23038 < 32768, so bit 15 is always 0 in a valid record.
- Erased flash `0xFFFF` has bit 15 = 1 → invalid. Also invalid: `day == 0xFFFF`, `minute > 1439`, `kind > 3`, `trigger > 2`.
- Invalid records are skipped on read (not an error).

Budget: 4 events/day × 365 days × 4 B = 5840 B ≈ 6 KB per year. Cap = 1536 records = 6144 B.

### 3. File `/logs.bin` (LittleFS)

- No header. Record count = `os.stat(LOG_PATH)[6] // 4` (VERIFY on device: `os.stat` size index, 08 V11).
- Append only: `open(LOG_PATH, "ab")`, write 4 bytes, close. One `write` per flush record.
- **Torn tail:** if size % 4 != 0, the trailing 1–3 bytes are ignored on read. `init_log()` truncates them (rewrite via tmp + rename).
- **Cap / compaction:** before appending n records, if `count + n > MAX_RECORDS` (1536):
  keep the newest `KEEP_RECORDS` = 1152 records (read via seek, tail only, R24), write them to `LOG_PATH + ".tmp"`, then `os.rename(tmp, LOG_PATH)`.
  Compaction happens about every 384 appends ≈ 96 days at 4 events/day.
- Power loss during compaction: old `/logs.bin` still intact. `.tmp` leftover is deleted by `init_log()`.
- Power loss during append: at most one torn record → ignored (see above).

### 4. RAM ring

- `RING_SIZE = 64`. Module-level list of tuples `(day, minute, kind, trigger)`.
- `record(day, minute, kind, trigger)`: skip if `day < 0` (before 2025-01-01, R24). Otherwise append. If `len == RING_SIZE` → `flush()` immediately (no data loss on busy days; one extra write, rare).
- `flush(path=LOG_PATH)`: pack all ring entries, append (with compaction check), clear ring on success. No-op when ring empty.
  On `OSError` (R24): keep only the newest `RING_SIZE` entries (drop oldest), keep the rest for the next try. Ring length never exceeds `RING_SIZE`.
- Flush also on ERROR entry and before `/api/reboot` reset (R29).
- **Midnight flush:** `ctrl.on_day_change(prev, new)` fires from `_read_clock()` when the local date changes (R4). `main.py` binds it to `logs.flush()`. Each entry carries its own `day`, so flush order does not matter and entries from before an RTC-unsynced period still write correctly.
- **Max loss on power loss = unflushed ring = events since last midnight (≤ 63).** Accepted (decision 6).

### 5. Hooks in `src/state.py` (no ctor change, no `logs` import)

| Hook | Event written | Trigger |
|------|---------------|---------|
| `_on_limit_reached()` | open or close (by `MOVING_OPEN/CLOSE`) | `self._trigger` |
| `_halt(SAFETY_STOP)` | safety_stop | `self._trigger` |
| `_halt(ERROR)` | error | `TRIG_AUTO` |

Callbacks (R4, R5). Defaults are no-ops; `main.py` assigns them:

- `self.on_event(kind, trigger)`: `kind` = str `"open"|"close"|"safety_stop"|"error"`, `trigger` = int `MovementTrigger`. Called from the three hook points.
- `self.on_day_change(prev, new)`: `prev` / `new` = `(y, mo, d)`. Called from `_read_clock()`, also in SAFETY_STOP / ERROR (R30: tick still reads the clock).

Rules:
- Record only if `self._last_local is not None` and `day >= 0`. Otherwise skip silently.
- `_last_local` is set in `_read_clock()`: `(y, mo, d, minute)` when synced (OSF=0 and y ≥ 2020), else `None`. Event minute is ≤ 2 s stale (tick period).
- Logging reads only `_last_local`, never `_date`. POST config/time sets `_date = None`; events keep recording.
- Date change check in `_read_clock()` (R30): `prev = _last_local[:3]`, not `_date`. If `_last_local` is not None and `(y, mo, d) != prev` → `self.on_day_change(prev, new)`. Then set `_last_local` and `_date`. A `_date` reset with unchanged date fires nothing. A reset plus a real date change flushes the old day.
- Both callbacks run inside `try/except Exception` (print, never raise into `tick()`) (R29).
- `_halt(ERROR)` records only when the state changes into ERROR: guard `self.state != ERROR` before the call (`_i2c_call` re-halts on every failed read; without guard one error logs per tick). Trigger `TRIG_AUTO`. `main.py` flushes on ERROR entry (R29).
- Boot-time `enter_error()` runs before any clock read: `_last_local` is None, so the ERROR is NOT recorded. By design: no clock, no timestamp. (The earlier note "bind before enter_error so ERROR is logged" was wrong.)
- `_halt(SAFETY_STOP)` only from `MOVING_*` (already the case in `tick()` / `_run_move`).
- Refused commands (`manual_move` returns False: 409 in SAFETY_STOP/ERROR, ignored same-direction move) are NOT logged.

### 6. Date helpers (pure integer math, no `time` module)

- `days_since_2025(y, m, d) -> int`: Howard Hinnant `days_from_civil` minus the value for 2025-01-01. 2025-01-01 → 0.
- `date_from_days(n) -> (y, m, d)`: Hinnant `civil_from_days`. Inverse of the above.
- Chosen because `time.mktime` differs between CPython (epoch 1970) and MicroPython (epoch 2000) and takes different tuple lengths. Pure int math works identically on both.

### 7. Read API

- `read_tail(n, path=LOG_PATH) -> list[tuple]` (R24): last `n` valid file records, oldest first. Count = `os.stat(path)[6] // 4`; seek to `(count - n) * 4` (or 0) and read only that span. Missing file → `[]`. The API and status use this.
- `read_events(path=LOG_PATH) -> list[tuple]`: whole file, oldest first. Tests and REPL only, never called by the API.
- `log_json(limit) -> list[dict]`: newest first. RAM ring entries (newest) first, then `read_tail` records newest to oldest, up to `limit`.

### 8. `GET /api/log` (R26)

- Fixed newest 50. NO query param (any query string is ignored). No 400 path.
- `log_json(50)`: RAM ring + `read_tail`. Max 50 × ~60 B JSON ≈ 3 KB RAM. Never the whole file.
- Response (JSON array, newest first):

```json
[{"date":"2026-10-12","time":"06:30","kind":"open","trigger":"auto"},
 {"date":"2026-10-12","time":"18:05","kind":"close","trigger":"auto"}]
```

- `kind` strings: `open`, `close`, `safety_stop`, `error`. `trigger` strings: `auto`, `manual`, `recovery`.
- `GET /api/status` adds key `"events": log_json(5)` (R2, newest first).

### 9. `init_log()`

- Missing `LOG_PATH` → create empty file.
- Delete leftover `LOG_PATH + ".tmp"` if present.
- Size % 4 != 0 → truncate to floor multiple of 4 (tmp + rename).
- Never resets existing content.
- Called from `boot.py` once per boot, before the control loop.

---

## Tests (TDD — write first; all must FAIL before implementation)

CPython, `tmp_path` fixture for file path, `monkeypatch` for `logs.LOG_PATH`. `tests/test_logs.py` replaces the old file entirely.

Pure codec:
1. `test_record_is_4_bytes` — `pack_record(...)` length == 4.
2. `test_pack_unpack_roundtrip` — parametrize minute ∈ {0, 1, 1439}, kind ∈ 0..3, trigger ∈ 0..2 → round trip equal.
3. `test_pack_rejects_out_of_range` — minute 1440, kind 4, trigger 3 → `ValueError`.
4. `test_erased_bytes_invalid` — `b"\xff\xff\xff\xff"` → `unpack_record` returns `None`.
5. `test_packed_bit15_reserved` — packed with bit 15 set → `None`.
6. `test_trigger_codes_match_state` — `logs.TRIG_*` == `state.MovementTrigger.*` (import `state` only in this test).

Date helpers:
7. `test_days_base` — `days_since_2025(2025,1,1) == 0`.
8. `test_days_year_boundary` — `days_since_2025(2026,1,1) == 365`.
9. `test_days_leap_day` — `days_since_2025(2028,3,1) == 1155` (hand-computed: 2025+2026+2027 = 1095 to 2028-01-01, +31 Jan +29 Feb 2028 → 1155; verify by run).
10. `test_date_roundtrip_2025_2035` — for every n in range(0, 3900): `days_since_2025(*date_from_days(n)) == n`.

File behaviour:
11. `test_init_creates_empty_file` — size 0 after `init_log`.
12. `test_init_does_not_reset_existing` — write 2 records, `init_log` again → still 2 records.
13. `test_append_and_read_order` — write 3 records via `flush` path, `read_events` returns them oldest first.
14. `test_read_missing_file_returns_empty` — `read_events(nonexistent)` == `[]`.
15. `test_torn_tail_ignored_on_read` — file = 2 valid records + 2 garbage bytes → `read_events` returns 2.
16. `test_init_truncates_torn_tail` — same file → after `init_log`, size == 8 and records intact.
17. `test_erased_record_skipped` — file contains one `\xff`×4 record between valid ones → skipped, valid ones returned.
18. `test_leftover_tmp_removed_by_init` — create `LOG_PATH+".tmp"` → `init_log` deletes it.

Ring + flush:
19. `test_record_buffers_in_ram` — 3 `record()` calls → file size 0, ring length 3.
20. `test_flush_writes_and_clears_ring` — after `flush()` → file has 3 records, ring length 0.
21. `test_flush_noop_when_empty` — `flush()` on empty ring → file unchanged.
22. `test_ring_full_triggers_flush` — 64 `record()` calls → file has 64 records, ring length 0.
23. `test_ring_never_exceeds_size` — 200 `record()` calls → ring length ≤ 64 at all times (assert inside loop).

Cap:
24. `test_cap_never_exceeds_max_bytes` — append `MAX_RECORDS + 10` records → file size ≤ `MAX_RECORDS * 4`.
25. `test_compaction_keeps_newest_in_order` — after overflow, last record = newest, first record = expected oldest kept, order monotonic.
26. `test_compaction_leaves_no_tmp` — no `.tmp` file after compaction.

API:
27. `test_log_json_newest_first_limit` — 5 events, `log_json(3)` → 3 newest, newest first; `date`/`time` strings correct (e.g. day 0 → "2025-01-01").
28. `test_log_json_includes_ram` — unflushed ring event appears first.
29. `test_api_log_real_data` (microdot test client, `tests/test_api.py` fixture) — GET `/api/log` returns list with 1 item after one record+flush.
30. `test_api_log_missing_file_returns_empty` — `[]`.
31. `test_api_log_fixed_50` (R26) — 60 records on file → GET `/api/log` returns the 50 newest, newest first; a query string (`?limit=3`) is ignored (still 50).
32. `test_read_tail_newest_only` (R24) — 100 records on file → `read_tail(5)` == last 5, oldest first.

State integration (CPython, `tests/mock_hardware.py`, `asyncio.run` for move tasks; events captured via `ctrl.on_event` / `ctrl.on_day_change` test stubs or bound `logs` functions):
33. `test_open_event_auto` — tick into schedule window → limit reached → `on_event("open", TRIG_AUTO)` → ring has `(day, minute, KIND_OPEN, TRIG_AUTO)`.
34. `test_manual_trigger_recorded` — `manual_move("open", source="web")` → event trigger `TRIG_MANUAL`.
35. `test_recovery_trigger_recorded` — from SAFETY_STOP, `manual_move("open", source="button")` → `TRIG_RECOVERY`.
36. `test_safety_stop_event` — `move_timeout_s=0` (or patched clock) → `KIND_SAFETY` recorded once.
37. `test_error_event_once` — 5 failing `_i2c_call`s → exactly 1 `KIND_ERROR` event (R29: only on transition into ERROR).
38. `test_no_event_when_rtc_unsynced` — mock RTC with OSF=1, or year < 2020 → `_last_local is None`, ring empty.
39. `test_date_change_flushes` — mock clock crosses midnight → `on_day_change(prev, new)` fires once, `prev` = `_last_local[:3]` (R30); bound to `logs.flush` → file gains records, ring cleared. After `_date = None` with the same date → callback NOT fired.
40. `test_event_logged_after_date_reset` — set `ctrl._date = None` (as POST config/time does), then limit reached → event still recorded from `_last_local`.
41. `test_hook_exception_does_not_stop_tick` (R29) — `on_event` raises → `tick()` returns normally, state correct.
42. `test_error_flushes_ring` (R29) — enter ERROR with ring non-empty → file gains records, ring empty.
43. `test_flush_oserror_caps_ring` (R24) — patch file open to raise `OSError`; 200 `record()` calls → no exception, ring length ≤ 64 at all times.
44. `test_record_skips_negative_day` (R24) — `record(-1, ...)` → ring length unchanged.

Run order: 1–18 first (codec + file), then 19–32, then 33–44. All fail until implemented.

---

## Verification

```bash
uv run pytest tests/test_logs.py -v          # all green
uv run pytest tests/test_state.py -v         # still green (hooks added)
uv run pytest tests/test_api.py -v           # still green (/api/log real)
uv run ruff check src/logs.py src/state.py src/web.py tests/test_logs.py
```

Device check after phase 8 (see `08-hardware-boot.md` section 5.10): midnight flush, power-cycle persistence, `/api/log` ordering.

## Open — VERIFY ON DEVICE

Single VERIFY table lives in `08-hardware-boot.md` (section "Open — VERIFY ON DEVICE"). Log items there:

- V11 `os.stat(path)[6]` is file size; `os.rename(tmp, LOG_PATH)` replaces an existing file (LittleFS).
- V16 `open(path, "ab")` append works on LittleFS (no `r+b` needed).
- V17 `struct.pack(">HH", ...)` available on MicroPython.
- V18 Power loss mid-append → only torn tail; mid-compaction → old file intact. Test build only: pull power during a forced flush loop.
- V19 Single append write time in ms (should be far below the 2 s tick).
