# Smart Coop V2 — owner decisions for phases 4-8 (final, 2026-10-12)

Source of truth for code that EXISTS: repo CLAUDE.md, src/state.py, src/config.py, src/astro.py, src/compat.py, tests/mock_hardware.py.
Docs must describe these, never the old design.

## Already implemented (phases 1-3) — describe, do not redesign
- No light sensor (BH1750 removed). No lux anywhere.
- Schedule: open = sunrise(-0.833°) + open_after_sunrise_min; close = civil dusk(-6°) + close_after_dusk_min. NOAA formula, ±2 min.
- Config dict: {"location":{"lat","lon"}, "schedule":{"open_after_sunrise_min","close_after_dusk_min"}, "safety":{"move_timeout_s"}}.
  load() never raises (defaults on error), save() atomic (tmp + rename), validate_config() raises ConfigError.
- CoopController(motor, rtc, limit_top, limit_bottom, nsleep, nfault, config). tick() every 2 s. manual_move(action).
  status_json(), get_forecast(days) -> [(open, close)], today_schedule, desired(now).
- Single move owner _start_move (gen counter). main.py NEVER spawns _run_move. _halt bumps gen, sets state, nSLEEP low first, motor.stop best-effort.
- Manual hold until next scheduled change. RTC unsynced (y<2020) -> no auto moves.
- Flat imports, MicroPython-safe (no enum/dataclasses/contextlib/get_running_loop).

## Decisions for phases 4-8 (to be implemented)
1. SAFETY_STOP cleared ONLY by physical button. manual_move gets a source: manual_move(action, source="button"|"web").
   Web in SAFETY_STOP -> refused. /api/manual returns 409 {"error":"SAFETY_STOP: clear with physical button at the coop"}. UI shows that text.
   ERROR: reboot only (/api/reboot allowed).
2. Buttons: action on RELEASE for press < 1 s (BTN_OPEN -> open, BTN_CLOSE -> close). Both buttons held 3 s -> start AP, no door move.
   Debounce press AND release (~50 ms). Hardware RC also added.
3. AP: SSID Szczesliwy_Kurczak, password from config (new config key, e.g. "wifi": {"ap_password"} min 8 chars, validated) — not hardcoded in repo.
   AP never starts while MOVING_*; deferred until idle. Idle timeout 10 min since last HTTP request (status polling counts as activity).
   AP teardown (ap.active(False), server shutdown) in finally.
4. Web: ONE page index.html, inline CSS + vanilla JS fetch, NO CDN (phone on AP has no internet). Polls /api/status every 3 s.
   Shows: state, time, rtc_synced, today open/close, limits, config_warning, last events (RAM log), 2 buttons, time sync button,
   config form, reboot button. Forecast: /api/forecast returns JSON [[open,close],...] fixed 14 days (or ?days 1..30 clamped); browser draws it.
   DROPPED: SSE /api/events, server-side SVG, debug.html, logs.html, config.html (merged into index), WebREPL.
5. API endpoints: GET /api/status (= ctrl.status_json + log tail), GET/POST /api/config (POST: dict check, validate_config, save,
   ctrl.config = d; ctrl._date = None), POST /api/time {timestamp_ms} (int, year 2024..2099 else 400; MicroPython epoch 2000 shift;
   clears OSF; ctrl._date = None), POST /api/manual?action=open|close (409 in SAFETY_STOP/ERROR), GET /api/forecast, GET /api/log,
   POST /api/reboot (respond first, reset after ~200 ms task). microdot Request.max_content_length ~2 KB. Bad JSON / wrong type -> 400 not 500.
   Test client: `from microdot.test_client import TestClient` (verify with context7 at implementation).
6. Log: RAM ring buffer of recent events (e.g. last 64), each event = (local date, minute, kind open|close|safety_stop|error, trigger auto|manual|recovery).
   Flushed to flash ONCE A DAY at local midnight as compact 4-byte records (day-of-year/year handled by a per-day header or 2B day + 2B minute|kind|trigger),
   append-only file, capped (~1 year ~6 KB, rotate/truncate oldest). Max data loss on reboot = current day. Flash wear is a non-issue
   (LittleFS wear levelling) — the reason for daily flush is fewer write windows for power loss.
   Logging hook: state.py records events in _on_limit_reached / _halt; midnight detection reuses _read_clock local date change.
7. RTC: DS3231 on ZS-042 board. OSF flag (status reg 0x0F bit 7): OSF=1 -> time untrusted -> treated as unsynced (no auto, prompts sync);
   set_datetime clears OSF and forces 24h mode (clear hour bit 6). VBAT ADC on GPIO3 REMOVED (battery drain through GPIO ESD diode).
   Yellow LED = "RTC not synced / OSF" (1 Hz) instead of low VBAT. CR2032 replace preventively every ~3 years.
   ZS-042 mod: remove charging resistor (~200 Ω, mandatory with CR2032) + power LED & resistor (~1 mA).
   v1 DS1307 drift compensation NOT needed (DS3231 TCXO ±2 ppm ≈ 1 min/yr); DS3231 aging offset reg 0x10 = calibration knob if ever needed.
8. Boot: FIRST line of boot.py `Pin(21, Pin.OUT, value=0)` (nSLEEP low), then short settle (~100-500 ms, v1 used 1 s), then PCF8574 single write
   0xF3 (P2/P3 motor IN = 0, LEDs off). I2C device missing -> do NOT loop in boot; continue to main, controller ends in ERROR (LED_RED 4 Hz),
   buttons/AP still usable. I2C bus recovery (9 SCL clocks) before declaring ERROR.
9. Watchdog: machine.WDT(timeout=30000), fed in control loop. Must be compatible with lightsleep (feed before sleep, sleep << timeout; VERIFY on device).
10. Power saving: machine.lightsleep() — YES. When idle (no MOVING_*, AP off, no button held): control loop does tick() then lightsleep(~2000 ms)
    instead of asyncio sleep. Wake sources: timer + BTN_OPEN/BTN_CLOSE GPIO (ESP32-C3 GPIO wake — exact API VERIFY with docs/device).
    While moving or AP active: no lightsleep (normal asyncio). ticks_ms continuity across lightsleep: VERIFY on device.
    CPU freq: fixed 80 MHz (machine.freq(80_000_000) in boot). No 40 MHz try, no AP-session bump (R16 superseded).
    Measure current with multimeter in phase 8 checklist (idle, moving, AP).
    PSU is mains 5-6 V; power saving is a project goal (low-power), not survival-critical.
11. Motor reversal: coast dead-time ~200 ms (motor.stop + sleep) before driving opposite direction (in _run_move when superseding a move).
12. INIT limit read: require 2 consecutive consistent ticks before deciding (glitch protection).
13. Hardware changes (hardware.md): 10k pull-down on nSLEEP (GPIO21 is UART0 TX, toggles during ROM boot); RC 10k/100nF on buttons and limit
    switches; disconnect VBAT->GPIO3; I2C address table only 0x20 (PCF8574) and 0x68 (DS3231); PCF power-on 0xFF = IN1=IN2=HIGH = BRAKE
    (fix the doc's contradiction) -> boot writes 0xF3 immediately; nSLEEP pull-down keeps driver asleep anyway.
14. Upload: flat files `mpremote fs cp src/x.py :x.py`, config.default.json -> :config.json, www/index.html -> :/www/index.html, main.py last.
15. Classes: no Button/LED wrapper classes with hasattr branching. Raw machine.Pin for GPIO; one small PcfLed helper for PCF LEDs; Motor via PCF;
    RTC DS3231 driver (datetime/set_datetime/osf). No LightSensor, no VBatADC.

## Cross-doc resolutions (round 2, final — apply everywhere)
R1 manual_move(action, source) — source REQUIRED ("button"|"web"), returns bool. Every caller passes source. Web IGNORES the bool:
   it checks ctrl.state: SAFETY_STOP/ERROR -> 409, else 200 (same-direction no-op = 200).
R2 Status JSON key `events` = logs.log_json(5) (newest first). The name `log_tail` is dropped.
R3 GET /api/log: newest first, RAM + file, ?limit 1..200 (default 50), 400 outside. api.md + 05 tests follow 06.
R4 Midnight flush: ONE mechanism = `ctrl.on_day_change(prev, new)` callback (default no-op), called from _read_clock when local date changes
   and prev is not None. main.py binds it to logs.flush(). state.py NEVER imports logs.
R5 Events: `ctrl.on_event(kind, trigger)` callback (default no-op) called from _on_limit_reached / _halt; main binds to logs.record.
   Logging must not depend on ctrl._date (POST config/time invalidates it): logs.record takes date/minute from the last known local clock
   cache `ctrl._last_local` = (y, m, d, minute), set by every successful _read_clock.
R6 Config schema gains "wifi": {"ap_password": ""} in DEFAULTS. Validation: "" (= open AP, no password) OR str 8..63 chars.
   Owner choice: AP is OPEN while password is empty (first boot / config lost). UI shows a prominent red banner "Set AP password"
   whenever it is empty; config form edits it (write-only: GET /api/config returns "ap_password_set": bool, never the password).
   api.md / README / 05 / 07 all include it; 05 "unknown key" test uses key `foo`.
R7 AP start: both buttons held 3 s. Single button held >= 1 s = no action. api.md fixed (no "double-press").
R8 Auto AP once per boot when RTC unsynced or OSF=1. Same 10 min idle timeout.
R9 INIT with neither limit and RTC unsynced -> home CLOSE (keep current code). Hold never expires while unsynced (documented).
R10 Web server start: use the microdot API confirmed by context7 (app.run / start_server — VERIFY); activity tracking = before_request
   hook calling touch_cb (05 design). 04 follows 05.
R11 Task names: button_task, led_task, ap_session, control_loop (04 names) everywhere incl. 08.
R12 Wiring: hardware.create_hardware() -> dict(ctrl_args, leds, btn_open, btn_close, config, boot_ok). main(hw) builds ctrl and,
   if not boot_ok, calls ctrl.enter_error() (public method, not _halt).
R13 control_loop(ctrl, wdt, btns, ap) — explicit params; _can_lightsleep(ctrl, btns, ap).
R14 lightsleep ONLY when state in IDLE_* / MANUAL_HOLD_* AND RTC synced AND AP off AND no button pressed. Never in INIT/MOVING/SAFETY_STOP/ERROR
   (LEDs must blink). 08 + hardware.md follow.
R15 GPIO wake: WAKEUP_ALL_LOW with two pins wakes only when BOTH are low -> wrong. Use a wake config that fires on EITHER button
   (VERIFY C3 API: per-pin wake / esp32.gpio_wakeup / ext1 ANY_LOW). Blocking VERIFY item before lightsleep ships; fallback = no lightsleep.
R16 SUPERSEDED (2026-10-12): CPU fixed 80 MHz at boot; AP session does not touch freq, nothing to restore.
R17 OSF: explicit rtc.osf() read in _read_clock (MockRTC has an osf flag); RTC.datetime() returns the true time, no 1999 trick.
   OSF=1 -> unsynced. set_datetime clears OSF + forces 24h.
R18 DS3231 missing at boot -> ERROR, LED_RED 4 Hz (08 correct, 04 fixed).
R19 Stop order everywhere: nSLEEP LOW first (GPIO kill switch), then IN1=IN2=LOW via PCF (best effort). Same for limit-stop.
R20 08 drops "decisions 11/12 not in state.py yet" lines (phase 4 builds them). 04 drops "run_local.py must still start" (phase 5 creates it).
R21 config.default.json -> :config.json ONLY on first flash (04, 08, README all say so). Upload dir `fs mkdir :/www`.
R22 RTC contract: datetime() returns 7-tuple (y, m, d, h, mi, s, wd); set_datetime(t) takes >= 6 fields.
R23 INIT LEDs all off; AP allowed during SAFETY_STOP (does not clear it).

## Review round 3 (2026-10-12, owner approved) — apply everywhere
R24 Log stays BINARY 4 B/event (owner reads logs once in years; compactness wins). Keep 06 format. /api/status and /api/log read
    only the file tail (seek size-4*n), never the whole file. Flush OSError -> drop oldest, ring len <= RING_SIZE. record() skips day < 0.
R25 Lightsleep deferred: ship asyncio sleep first; lightsleep enabled only after V-wake (either button) passes on device.
    When enabled: `await asyncio.sleep(0)` after every lightsleep (other tasks must run). Gate also requires `not ap_request`.
R26 Simplify: plan 04 tests ~72 -> ~40 (merge/parametrize, drop duplicates of test_state/test_config, drop source-grep tests);
    05 ~38 -> ~26. /api/log = fixed newest 50, NO query param. /api/forecast = fixed 14 days, NO query param.
    No ApHandle class, no hw dict/create_hardware factory: main.py builds hardware directly, AP flags = module globals in main.py
    (ap_request, ap_active, ap_auto_done). One fake `machine` object injected into control_loop/ap_session instead of compat shims.
R27 main.py spec owned ONLY by 04 §11. 08 references it, never redefines. One VERIFY table (in 08), 04 references by V-id.
    asyncio: create_task for each task + one await; each task body wrapped: except Exception -> print, logs flush best-effort, machine.reset().
R28 LEDs: one API for all three (`.value(v)`; PcfLed gets value()). led_task(ctrl, red, green, yellow).
R29 Hooks on_event/on_day_change always called in try/except Exception (never kill tick). on_event for ERROR fires once
    (guard in _halt: only when state changes to ERROR). ERROR trigger = AUTO. Flush on entering ERROR and before /api/reboot reset.
R30 tick in SAFETY_STOP/ERROR still calls _read_clock (day change, rtc_synced, _last_local), never moves.
    on_day_change prev taken from _last_local[:3], not _date.
R31 rtc_synced starts None; auto-AP only on explicit False. Auto-AP also when boot ends in ERROR (status visible without cable).
R32 rtc.osf() read via _i2c_call. MockRTC gets osf flag/method BEFORE state.py uses it. status_json uses cached rtc_synced, no extra RTC read.
R33 /api/time: convert ts with pure-int civil_from_days (no gmtime/epoch shift); range check AFTER CET conversion (2024..2099 CET).
    set_datetime OSError -> 500 {"error":"rtc write failed"}.
R34 Buttons: debounce release AND the other button; failed release-stable keeps original t0/both_t0 (bounced long press = no action).
R35 AP idle watchdog = inline check in ap_session poll loop (no separate task), created/checked BEFORE awaiting server.
R36 PCF cache: Motor.stop clears IN bits in cache BEFORE write attempt (a later LED write never re-asserts IN1/IN2).
    Reversal with PCF OSError -> ERROR (same as _run_move drive failure).
R37 INIT: 2 consistent reads for ALL outcomes (top, bottom, both, neither). manual_move allowed from INIT (MANUAL trigger) — document + test.
R38 Hold while unsynced / no-auto day (_hold_desired None) expires on first valid desired — documented, intended.
R39 UI: "clear password (open AP)" checkbox sends ap_password "". Blank field = keep.
R40 Single strings/sources: /api/log has no 400 now (R26). Tests use pytest-asyncio asyncio_mode=auto (already in pyproject).
    Prereqs: wifi.ap_password + manual_move(source) come from phase 4. ap_session belongs to phase 4. WDT created in main.py.
    boot.py -> main.py: no module-global boot_ok; main.py scans I2C itself. Midnight device test: give exact UTC timestamp_ms, note CEST.
    CPU fixed 80 MHz; earlier 80 MHz AP-connect panic was IDF heap OOM, fixed by .mpy deploy (R41). 40 MHz dropped, 08 V1 and V14 moot.

R41 Deploy as .mpy, captive DNS, CPU 80 MHz (2026-10-12). Supersedes item 14 upload method.
- Deploy = `uv run python scripts/deploy.py COM<N> [--first]` (.mpy, mpy-cross 1.29.0.post2). Also uploads boot.py (plain .py) and index.html.gz (gzip -9, mtime 0). config.json only with --first; :config.json and :logs.bin otherwise untouched.
- Why .mpy: on-device .py compile grows MP split heap into IDF DRAM, never shrinks. IDF free before ap_on: .py 3.6 KB vs .mpy 104 KB. With .py: AP gave "WiFi Out of Memory" on client connect.
- Captive DNS (src/dns.py): all A queries → 192.168.4.1 while AP up; non-/api 404 → 302 to http://192.168.4.1/. Any hostname works except .local (mDNS bypasses). Verified on phone. Bare board (RTC unsynced): AP auto-starts ~14 s after boot.
- CPU fixed 80 MHz; earlier 80 MHz AP-connect panic was IDF heap OOM, fixed by .mpy deploy (R41).

R42 Remote update via WebREPL, AP session only (2026-10-12). Reverses the WebREPL drop in item 4.
- config `wifi.webrepl_password`: "" = disabled (default); else 4..9 printable ASCII (modwebrepl.c buffer 10 B). Masked in GET /api/config like ap_password.
- ap_session: webrepl.start(password=) after ap_on, webrepl.stop() in finally before ap_off. Never at boot (R8 radio off). Start failure only prints; AP stays.
- Host: `uv run python scripts/deploy.py --wifi PASS [--host 192.168.4.1]` (scripts/webrepl_client.py, from micropython/webrepl, MIT). Uploads .mpy, index.html.gz, boot.py, main.py last; never config.json/logs.bin; cannot delete stale .py → first deploy always USB.
- Measured 2026-10-12 (80 MHz, bare board): 14 files over WebREPL OK, then reboot via POST /api/reboot (REPL-text machine.reset() ignored while app runs). After reboot: mhz 80, MP mem free 52 KB.
- Risk: WebREPL = remote code execution for anyone with the password; RAM cost on device unmeasured (VERIFY).
- deploy --wifi GETs /api/status before each file to hold the AP idle timer; partial upload = USB recovery. Password change applies at next AP session.

## Doc style
- English, terse, tables/bullets. Each phase doc: Goal, Deliverables (files), Design, Tests (TDD — what fails first), Verification
  (exact commands / device checklist), Open "VERIFY ON DEVICE" items. Flat imports in every code snippet.
- Mark every hardware/library claim not verified as "VERIFY". Don't invent library APIs — use context7 for microdot / MicroPython if you cite APIs.
