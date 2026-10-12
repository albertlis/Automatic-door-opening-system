# CONVENTIONS

## Layout
- `src/` — MicroPython runtime, FLAT (uploads to device root). One module per concern, no packages.
- `src/www/` — static UI (`index.html` only, inline CSS/JS).
- `tests/` — pytest; `test_<module>.py` per `src/<module>.py`; `mock_hardware.py` = fakes, `conftest.py` = fixtures.
- `docs/` — design notes; `docs/phases/NN-<topic>.md` = phase specs; `docs/decisions.md` = decision log.
- `legacy/` — v1 Arduino archive. Read-only. Not subject to these rules.
- `run_local.py` — CPython dev runner. Root stays this small.

## Naming (measured: ~95% of src symbols already follow)
- Files/modules: short lowercase noun, one word: `astro.py`, `logs.py`.
- Classes: PascalCase noun: `CoopController`, `PcfLed`. Hardware chips keep part number: `PCF8574`.
- Constant groups: plain class, UPPER members: `GateState.IDLE_OPEN`.
- Functions/methods: snake_case verb or verb phrase: `manual_move`, `read_tail`, `validate_config`.
  Pure converters may be `<x>_from_<y>` / `<x>2<y>`: `date_from_days`, `bcd2dec`.
- Private: leading `_`: `_start_move`, `_ring`.
- Module constants: UPPER_SNAKE with unit suffix where physical: `AP_IDLE_MS`, `move_timeout_s` (config keys lowercase).
- Booleans: predicate: `is_dst`, `_timed_out`, `ap_active`. Module flag constant: `IS_*`/`*_OK`.
- Async loops: `<thing>_task` or `<thing>_loop`: `button_task`, `control_loop`.
- Tests: `test_<behaviour>` functions, no test classes required.
- Config/JSON keys: lowercase snake: `open_after_sunrise_min`.
- Web routes: `/api/<noun>`; handlers `api_<noun>[_<verb>]`: `api_config_post`.

## Vocabulary
- gate — the door mechanism / state machine — banned: door (in code), hatch
- state — `GateState` value — banned: status (except `/api/status` payload), mode
- trigger — `MovementTrigger` value (AUTO/MANUAL/RECOVERY), why a move happened — banned: reason, cause
- source — who pressed manual (`"button"`, `"web"`) — banned: origin, caller
- action / direction — `"open"`/`"close"` — banned: cmd, dir
- kind — log event type (open/close/safety_stop/error) — banned: type, evt
- ctrl — `CoopController` instance (only allowed abbrev for it) — banned: controller, coop, c
- cfg — config dict — banned: conf, settings
- day — days since 2025-01-01 (logs); minute — local minute of day — banned: ts, stamp
- schedule — (open_min, close_min) for a date — banned: times, window
- Allowed abbrevs: ctrl, cfg, rtc, pcf, i2c, ap, led, btn(s), ms, s, min, gen, mp.

## Banned names
`utils*`, `helper*`, `misc`, `common`, `new_*`, `old_*`, `tmp_*`, `final*`, `*_v2`, bare `data`/`info`/`manager`/`handler`/`process`,
single-letter names at module level, cryptic 1-3 letter privates (`_p`, `_fmt` ok only if local helper < 10 lines).

## Where new code goes
- Sun/date math → `astro.py`. Calendar int math (days<->date) → `astro.py`, nowhere else.
- Config key / validation → `config.py` (+ `config.default.json`, `DEFAULTS`).
- State transition / move logic → `state.py` (`CoopController`). Enum-like constants → `state.py`.
- Event log storage/format → `logs.py`.
- Chip driver / pin / AP radio → `hardware.py`.
- asyncio tasks, wiring → `main.py`. Boot-time pin safety → `boot.py`.
- HTTP endpoint → `web.py` `create_app`. UI → `src/www/index.html`.
- Fake hardware for tests → `tests/mock_hardware.py`.
- New module only if none above fits AND it is >~100 lines.
