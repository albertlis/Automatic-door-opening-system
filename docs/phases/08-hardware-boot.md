# Phase 8 — Hardware Drivers, Boot, Device Verification

## Goal

Real MicroPython drivers for the parts `state.py` already uses through DI:
`Motor`, `RTC` (DS3231). Plus `PCF8574`, `PcfLed`, raw `Pin` for GPIO.
`main.py` builds the objects directly (R26). Write `boot.py` (no infinite loops).
Verify on the physical ESP32-C3 with the checklist in section 5. The single VERIFY table is at the end of this file.

## Ownership (R27)

- `main.py`, `control_loop`, `_can_lightsleep`, `button_task`, `led_task`, `ap_session`, task names, on_event / on_day_flush (04 §11), asyncio wiring: owned by **04 §11**. This doc does not define them.
- Log format, `logs.py`, callbacks: 06.
- State machine: `docs/state-machine.md`, `src/state.py`.

No automated tests for the device path. Driver logic that is pure bytes/BCD
has CPython tests with a fake I2C (see Tests below).

## Prerequisites

Hardware (decision 13, applied before first power-on):

| Item | Action |
|------|--------|
| DS3231 ZS-042 | Desolder charging diode + charging resistor (mandatory with CR2032). Remove power LED + its resistor (~1 mA). |
| CR2032 | Non-rechargeable. Replace preventively ~every 3 years. |
| nSLEEP (GPIO21) | 10 kΩ pull-down to GND. GPIO21 is UART0 TX and toggles during ROM boot. |
| BTN_OPEN (GPIO7), BTN_CLOSE (GPIO10), LIMIT_TOP (GPIO0), LIMIT_BOTTOM (GPIO1) | RC 10 kΩ / 100 nF to GND each (debounce). |
| VBAT ADC | Disconnect DS3231 VBAT → GPIO3. Battery drain through GPIO ESD diode. GPIO3 unused. |
| PCF8574 | A0=A1=A2=GND → address 0x20. |

Software:
- MicroPython flashed on ESP32-C3 (`esptool` commands in section 6).
- Phases 1–7 green on CPython.

---

## 1. Pin map and devices

| Signal | GPIO / bus | Access | Note |
|--------|------------|--------|------|
| I2C SDA | 4 | `hardware.get_i2c()` | only 0x20, 0x68 on bus |
| I2C SCL | 5 | same | |
| LIMIT_TOP | 0 | `Pin(0, Pin.IN, Pin.PULL_UP)` | active-low, 0 = at top |
| LIMIT_BOTTOM | 1 | `Pin(1, Pin.IN, Pin.PULL_UP)` | active-low, 0 = at bottom |
| LED_RED | 6 | `Pin(6, Pin.OUT)` | active-high, direct |
| BTN_OPEN | 7 | `Pin(7, Pin.IN, Pin.PULL_UP)` | active-low |
| BTN_CLOSE | 10 | `Pin(10, Pin.IN, Pin.PULL_UP)` | active-low |
| nFAULT (DRV8833) | 20 | `Pin(20, Pin.IN)` | active-low |
| nSLEEP (DRV8833) | 21 | `Pin(21, Pin.OUT)` | 0 = sleep. 10 kΩ pull-down |
| GPIO2, 3, 8, 9 | — | unused | 2/8/9 strapping; 3 VBAT removed |
| GPIO18/19 | — | USB D-/D+ | leave alone |

PCF8574 (0x20), one byte, all writes through `hardware.get_pcf()`:

| Bit | Signal | Level |
|-----|--------|-------|
| P0 | LED_GREEN | active-low |
| P1 | LED_YELLOW | active-low |
| P2 | Motor IN1 | 1 = forward (open) |
| P3 | Motor IN2 | 1 = backward (close) |
| P4–P7 | unused | written 1 |

Power-on value of PCF8574 = 0xFF → IN1=IN2=HIGH = **brake**. Boot writes `0xF3` immediately (P0,P1 = LEDs off; P2,P3 = 0 = coast; P4–P7 = 1).

DS3231 (0x68): registers 0x00–0x06 time, 0x0E control, 0x0F status.

---

## 2. `src/hardware.py` (MicroPython only)

Never imported on CPython. Tests use a fake I2C (see Tests).

```python
# hardware.py — MicroPython only
from machine import I2C, Pin
import time

_i2c = None
_pcf = None


def get_i2c():
    """Singleton I2C(0). Boot and main share it."""
    global _i2c
    if _i2c is None:
        _i2c = I2C(0, sda=Pin(4), scl=Pin(5), freq=400_000)
    return _i2c


def recover_bus():
    """Slave stuck holding SDA: 9 SCL pulses, then STOP. Caller must re-init via get_i2c()."""
    global _i2c, _pcf
    _i2c = None
    _pcf = None
    scl = Pin(5, Pin.OPEN_DRAIN, value=1)  # VERIFY open-drain on C3 (V6)
    sda = Pin(4, Pin.OPEN_DRAIN, value=1)
    for _ in range(9):
        scl.value(0)
        time.sleep_us(10)
        scl.value(1)
        time.sleep_us(10)
    sda.value(0)
    time.sleep_us(10)
    sda.value(1)          # STOP: SDA rises while SCL is high
    time.sleep_ms(10)


def get_pcf():
    global _pcf
    if _pcf is None:
        _pcf = PCF8574(get_i2c(), 0x20)
    return _pcf


def bcd2dec(v):
    return (v >> 4) * 10 + (v & 0x0F)


def dec2bcd(v):
    return ((v // 10) << 4) | (v % 10)


class PCF8574:
    def __init__(self, i2c, addr=0x20):
        self._i2c = i2c
        self._addr = addr
        self.out = 0xFF            # chip power-on value (V9 read-back at boot)

    def write(self, value):
        value &= 0xFF
        self._i2c.writeto(self._addr, bytes((value,)))
        self.out = value           # cache only after successful write

    def read(self):
        return self._i2c.readfrom(self._addr, 1)[0]


class PcfLed:
    """One PCF8574 bit as an LED. Same API as Pin: value(1) = LED on (R28).
    active_low: LED on when pin = 0 (handled inside)."""
    def __init__(self, pcf, bit, active_low=True):
        self._pcf = pcf
        self._mask = 1 << bit
        self._active_low = active_low

    def value(self, v):
        on = bool(v)
        pin_high = (not on) if self._active_low else on
        out = (self._pcf.out | self._mask) if pin_high else (self._pcf.out & ~self._mask)
        self._pcf.write(out)


class Motor:
    """DRV8833 via PCF8574 P2/P3. One I2C write per call: both pins change atomically,
    so IN2-low-before-IN1-high is satisfied in a single transaction."""
    def __init__(self, pcf, in1=2, in2=3):
        self._pcf = pcf
        self._m1 = 1 << in1
        self._m2 = 1 << in2

    def _drive(self, in1, in2):
        out = self._pcf.out & ~(self._m1 | self._m2)
        if in1:
            out |= self._m1
        if in2:
            out |= self._m2
        self._pcf.write(out)

    def forward(self):     # open: IN1=1, IN2=0
        self._drive(1, 0)

    def backward(self):    # close: IN1=0, IN2=1
        self._drive(0, 1)

    def stop(self):        # coast: IN1=0, IN2=0 (worm gear self-brakes)
        # R36: clear IN bits in the cache BEFORE the write. A failed write cannot leave
        # IN1/IN2 in the cache, so a later PcfLed write never re-asserts them.
        # nSLEEP is already LOW here (R19), so coast state is safe even if the write fails.
        self._pcf.out &= ~(self._m1 | self._m2)
        self._pcf.write(self._pcf.out)


class RTC:
    """DS3231. datetime() = true time (no sentinel values). OSF read separately."""
    ADDR = 0x68

    def __init__(self, i2c):
        self._i2c = i2c

    def datetime(self):
        """(y, mo, d, h, mi, s, wd), wd Mon=0 (DS3231 reg 1..7 minus 1). 24h mode assumed: set_datetime() always forces it."""
        b = self._i2c.readfrom_mem(self.ADDR, 0x00, 7)
        return (
            bcd2dec(b[6]) + 2000,
            bcd2dec(b[5] & 0x1F),
            bcd2dec(b[4]),
            bcd2dec(b[2] & 0x3F),
            bcd2dec(b[1]),
            bcd2dec(b[0] & 0x7F),
            (b[3] & 0x07) - 1,      # reg 1..7 (Mon=1) -> wd Mon=0 (R22, 05 §2.3)
        )

    def osf(self):
        """Status 0x0F bit 7. True = time untrusted -> state treats RTC as unsynced."""
        return bool(self._i2c.readfrom_mem(self.ADDR, 0x0F, 1)[0] & 0x80)

    def set_datetime(self, dt):
        """dt: >= 6 fields (y, mo, d, h, mi, s[, wd]). Clears OSF, forces 24h mode."""
        y, mo, d, h, mi, s = dt[:6]
        wd = dt[6] + 1 if len(dt) > 6 else 1   # wd Mon=0 -> DS3231 reg Mon=1..Sun=7
        buf = bytes((
            dec2bcd(s) & 0x7F,      # CH=0: oscillator running
            dec2bcd(mi),
            dec2bcd(h) & 0x3F,      # bit6=0: 24h mode
            wd,                     # day-of-week reg 1..7 (Mon=1), from wd Mon=0
            dec2bcd(d),
            dec2bcd(mo),
            dec2bcd(y - 2000),
        ))
        self._i2c.writeto_mem(self.ADDR, 0x00, buf)
        st = self._i2c.readfrom_mem(self.ADDR, 0x0F, 1)[0]
        # clear OSF (bit7) and alarm flags A1F/A2F (bits 0,1, write-0-to-clear). Keep bits 2..6.
        self._i2c.writeto_mem(self.ADDR, 0x0F, bytes((st & 0x7C,)))
```

Notes:
- No `create_hardware()`, no hw dict, no `boot_ok` global (R26, R40). `main.py` builds objects directly; wiring owned by 04 §11.
- get_i2c / get_pcf / recover_bus: module-level singletons (code above). boot.py and main.py share them, so the 0xF3 PCF cache is shared.
- Constructors used by main: `Motor(get_pcf(), in1=2, in2=3)`, `RTC(get_i2c())`, `PcfLed(get_pcf(), bit, active_low=True)` for P0/P1, `Pin(...)` per section 1 table.
- LEDs (R28): one API for all three. `red.value(v)` (Pin), `green.value(v)` / `yellow.value(v)` (PcfLed). `v` truthy = LED on.
- `RTC.datetime()` returns 7-tuple (R22), wd Mon=0. `state._read_clock` uses `r[0..4]` and `rtc.osf()` (R17). `status_json` uses `r[3], r[4]`.
- `Motor.__init__` has no I/O (boot already wrote 0xF3).
- Stop order (R19): nSLEEP LOW first (GPIO kill switch, no I2C needed), then `motor.stop()` via PCF, best effort. Same order on limit stop. Implemented in `state.py` (phase 4).
- Reversal (R36): OSError from `stop()` or the drive write → ERROR (same as `_run_move` drive failure).

---

## 3. `src/boot.py` — exact order

Runs on every reset before `main.py`. No `while True`. Each step either succeeds or records failure and continues. No flags are passed to `main.py` (R40): main scans I2C itself.

```python
# boot.py — runs before main.py on every reset
from machine import Pin

# 1. nSLEEP LOW first line. Driver asleep before anything else runs.
Pin(21, Pin.OUT, value=0)

# 2. Settle (100-500 ms; v1 used 1 s).
import time
time.sleep_ms(300)                     # VERIFY window (V12)

# 3. CPU fixed 80 MHz; earlier 80 MHz AP-connect panic was IDF heap OOM, fixed by .mpy deploy (R41). Never changed at runtime.
import machine
machine.freq(80_000_000)

# 4. Radio off at boot. AP is started by ap_session (04 §11), never by boot (decision 3, R8).
import network
network.WLAN(network.AP_IF).active(False)
network.WLAN(network.STA_IF).active(False)   # VERIFY STA off needed (V10)

# 5. I2C scan. One bus recovery attempt if 0x20 or 0x68 missing. No flag: main.py scans again (R40).
import hardware
found = hardware.get_i2c().scan()
if not ({0x20, 0x68} <= set(found)):
    hardware.recover_bus()

# 6. PCF8574 first write: motor coast, LEDs off (chip power-on = 0xFF = brake).
try:
    hardware.get_pcf().write(0xF3)
except OSError:
    print("boot: PCF8574 write failed")   # main.py re-checks the bus (R40)

# 7. Event log file.
import logs
logs.init_log()

# Boot done. MicroPython now runs main.py.
```

Missing I2C device at boot: boot continues, no flag set. `main.py` (04 §11) scans I2C, calls `ctrl.enter_error()` (R12, R18) → ERROR, LED_RED 4 Hz. Auto AP also starts when boot ends in ERROR (R31). No blink loop.

---

## 4. `src/main.py` — owner 04 §11

Not defined here (R27). The construction block, callback binding, I2C scan and task wiring live in 04 §11 only.

Hardware facts that main relies on (this doc owns them):
- Constructing `Pin(21, Pin.OUT)` without `value=` keeps the LOW level set by boot. VERIFY (V13).
- Degraded runtime: RTC missing → `_read_clock` returns None → after 3 failed reads → ERROR (state.py). PCF missing → first move raises OSError in the move task → ERROR (state.py), plus the boot-time `enter_error()` from 04 §11.
- ERROR set at boot (before any clock read) is not logged: no `_last_local` yet (06 §5).
- WDT is created in main.py (see Watchdog).

### Watchdog

- `machine.WDT(timeout=30_000)` created in `main.py` (not boot), so slow boot steps cannot trip it.
- `control_loop` (04 §11) calls `wdt.feed()` every tick and before any lightsleep.
- Timeout 30 s vs tick 2 s: ≥ 10× margin.
- `WDT(timeout=...)` and `feed()` API: confirmed in MicroPython ESP32 docs (Context7 `/micropython/micropython`, "Enable and feed WDT").
- VERIFY: WDT keeps running across `lightsleep`; feed before sleep is enough (V2).

### Light sleep (deferred, R25)

- **Off** until V3 + V4 (V-wake: either button) pass on device. Until then asyncio sleep only.
- Gate and sleep call live in 04 §11 (`_can_lightsleep`). Gate = IDLE_* / MANUAL_HOLD_* + RTC synced + AP off (`not ap_request`) + both buttons released (R14, R25). Never in INIT, `MOVING_*`, `SAFETY_STOP`, `ERROR`.
- When enabled: `await asyncio.sleep(0)` after every lightsleep (other tasks must run).
- Wake sources: timer + GPIO on EITHER button. Rejected: ALL_LOW wake (fires only when BOTH pins are low, R15).
- Candidates for the either-button wake (V3): per-pin wake, `esp32.gpio_wakeup`, `ext1` ANY_LOW.
- Why GPIO wake is mandatory: timer-only wake every 2 s would miss a sub-1 s tap (decision 2).
- Open: `ticks_ms` continuity across lightsleep (V5).

### Clock

- Fixed 80 MHz (`machine.freq(80_000_000)` in boot). USB 5V powered; no runtime freq switching.
- AP session (04 §11) does not change freq. WiFi runs at the 80 MHz boot clock (V14); earlier 80 MHz AP-connect panic was IDF heap OOM, fixed by .mpy deploy (R41). R16 superseded.

---

## 5. Hardware verification checklist (device)

Mark each item on the physical board. Record measured values in the table.

### 5.1 Boot and bus

- [ ] Power-on: GPIO21 measured LOW before boot.py line 1 completes (scope/logic analyser; VERIFY method, V7).
- [ ] Power-on: motor does not move, no buzz. PCF read-back `hardware.get_pcf().read() == 0xF3` (REPL).
- [ ] `hardware.get_i2c().scan()` → `[32, 104]` (0x20, 0x68) only.
- [ ] `boot.py` finishes; no blink loop; REPL prompt reachable.
- [ ] Unplug DS3231 → reboot → ERROR, LED_RED 4 Hz; buttons and AP still work; auto AP starts (R31, boot ends in ERROR).
- [ ] Unplug PCF8574 → reboot → same ERROR behaviour.
- [ ] Bus recovery: short SDA to GND for 1 s during runtime, release → reads resume without reboot (VERIFY; if not, reboot is the fallback).

### 5.2 Limit switches and INIT

- [ ] Press LIMIT_TOP → `/api/status` `limit_top: true`.
- [ ] Press LIMIT_BOTTOM → `limit_bottom: true`.
- [ ] Both pressed at boot → state ERROR (broken-limit boot test).
- [ ] Neither pressed, RTC synced → INIT homes toward `desired()`.
- [ ] INIT LEDs all off (R23).
- [ ] INIT decides only after 2 consecutive consistent tick reads, for all four outcomes (top, bottom, both, neither) (R37, decision 12): a LIMIT_TOP pulse shorter than one tick does not decide.
- [ ] Button press during INIT → MANUAL move accepted (R37), `source="button"`.

### 5.3 Schedule (via `/api/time`)

- [ ] Compute today's open minute from `/api/status` `today_schedule.open`. POST `/api/time` with a timestamp 1 min before it. Within 2 s + ≤ 21 s door reaches top → `IDLE_OPEN`.
- [ ] Same for close → `IDLE_CLOSED`.
- [ ] `/api/forecast` (fixed 14 days, R26) open/close match sunrise/dusk ±2 min against an external reference for the configured lat/lon (reference source: VERIFY, record it).
- [ ] `/api/time` rejects CET year < 2024 or > 2099 with 400 (range checked after CET conversion, R33).

### 5.4 Safety

- [ ] Block motor mid-travel → SAFETY_STOP after `move_timeout_s` (21 s). LED_RED 1 Hz.
- [ ] Disconnect LIMIT_BOTTOM during close → SAFETY_STOP at timeout (broken-limit runtime test).
- [ ] Briefly short nFAULT (GPIO20) to GND → SAFETY_STOP immediately (before timeout).
- [ ] In SAFETY_STOP: `/api/manual` → 409 with text `SAFETY_STOP: clear with physical button at the coop` (decision 1, R1).
- [ ] Physical button in SAFETY_STOP → RECOVERY move → reaches limit → `IDLE_*`.

### 5.5 Buttons (decision 2, R7)

- [ ] Tap BTN_OPEN < 1 s → on release → open move (`source="button"`), `MANUAL_HOLD_OPEN` at limit. Event `open` with trigger `manual` in log.
- [ ] Tap BTN_CLOSE < 1 s → close equivalent.
- [ ] Single button held ≥ 1 s → no action on release.
- [ ] Both held 3 s → AP starts, no door move.
- [ ] Bounce test: 20 quick taps → no double action (debounce press + release ~50 ms).

### 5.6 Reversal

- [ ] During MOVING_CLOSE press BTN_OPEN → motor stops, ~200 ms coast, then opens. Measure gap (logic analyser on nSLEEP/IN pins; VERIFY).
- [ ] Gap ≥ 200 ms on supersede (decision 11).
- [ ] Reversal with PCF write failure → ERROR (R36).

### 5.7 Web / AP (R3, R6, R8, R16, R23)

- [ ] Press during MOVING: AP deferred until idle (decision 3).
- [ ] AP starts during SAFETY_STOP (R23) and does not clear SAFETY_STOP.
- [ ] Connect `Szczesliwy_Kurczak` → `http://192.168.4.1` → status page polls every 3 s.
- [ ] Status polling resets idle timer; no polling 10 min → AP off, `ap.active(False)`, CPU clock untouched (fixed 80 MHz).
- [ ] `ap_password` empty → AP open (no password), red "Set AP password" banner shown.
- [ ] Set `ap_password` (8–63 chars) via config form → AP requires it. `GET /api/config` shows `ap_password_set: true`, never the password (R6).
- [ ] Time sync button → `/api/status` `time` matches phone within 1 min.
- [ ] Config POST → power cycle → config persists (`/config.json`).

### 5.8 OSF (decision 7, R8, R17, R31)

- [ ] Remove CR2032 and power off ≥ 1 min. Power on → `/api/status` `rtc_synced: false`, no auto moves, yellow LED 1 Hz.
- [ ] `rtc_synced` starts None (R31). Auto AP starts once at boot only on explicit False (RTC unsynced / OSF=1, R8), or when boot ends in ERROR. Idle timeout 10 min.
- [ ] Read `0x0F` bit 7 = 1 (REPL: `hardware.RTC(hardware.get_i2c()).osf()`).
- [ ] `/api/time` → `osf()` False afterwards; `rtc_synced: true`.

### 5.9 Light sleep and power (measure, record)

Measurement: multimeter in series on the 5 V supply line (mains PSU). Record each state.

| Condition | Clock | Current (mA) | Method | Date |
|-----------|-------|--------------|--------|------|
| Idle, awake (no sleep) | 80 MHz | | | |
| Idle, lightsleep 2000 ms (after V3/V4) | 80 MHz | | | |
| Moving open (no load) | 80 MHz | | | |
| Moving close (no load) | 80 MHz | | | |
| AP active, page open | 80 MHz | | | |
| AP idle after 10 min (before shutdown) | 80 MHz | | | |

- [ ] nSLEEP measured LOW in idle (~2 µA quiescent at driver; multimeter cannot resolve; scope or note).
- [ ] During move: nSLEEP HIGH ~1 ms before motor drive begins.
- [ ] Idle lightsleep (only if V3/V4 passed): wake by timer and by BTN press (either button); tap detected. Skip otherwise.
- [ ] Lightsleep never entered outside IDLE_* / MANUAL_HOLD_* (INIT, MOVING, SAFETY_STOP, ERROR, AP active): add counter print in test build.
- [ ] Watchdog: test build blocks loop > 30 s → reset observed. Normal operation 1 h → no reset.

### 5.10 Log (phase 6)

- [ ] After several open/close cycles → `/api/log` shows entries, newest first, correct date and time.
- [ ] Midnight flush (R40): POST `/api/time` with `{"timestamp_ms": 1791842390000}`. That is 2026-10-12 21:59:50 UTC (verified with `date -u`). `/api/time` converts UTC → CET (+1 h) → RTC 22:59:50 CET = local 23:59:50 on 2026-10-12, because local = CET + 1 h while CEST. Wait ~20 s → local date rolls to 2026-10-13 → `/logs.bin` size grows by that day's records; ring empty (REPL or `/api/log` ordering).
- [ ] CEST note: the timestamp above assumes CEST (UTC+2). CEST holds until 2026-10-25 (`is_dst` in `src/astro.py`: summer time ends last Sunday October, 02:00 CET). Recompute the timestamp after that date.
- [ ] Power-cycle → `/api/log` still shows flushed days. Today's unflushed events are gone (expected).
- [ ] `/logs.bin` size after several days: multiple of 4; ≤ 6144 bytes.

---

## 6. Upload and flash (flat, `mpremote`)

Erase and flash MicroPython (first time only):

```bash
esptool --chip esp32c3 --port COM<N> erase_flash
esptool --chip esp32c3 --port COM<N> --baud 460800 write_flash -z 0x0 ESP32_GENERIC_C3-*.bin
```

Create the web dir, then upload. Flat names on device root. `main.py` last (it starts the app).

```bash
mpremote connect COM<N> fs mkdir :/www
mpremote connect COM<N> fs cp src/compat.py      :compat.py
mpremote connect COM<N> fs cp src/astro.py       :astro.py
mpremote connect COM<N> fs cp src/config.py      :config.py
mpremote connect COM<N> fs cp src/state.py       :state.py
mpremote connect COM<N> fs cp src/logs.py        :logs.py
mpremote connect COM<N> fs cp src/hardware.py    :hardware.py
mpremote connect COM<N> fs cp src/web.py         :web.py
mpremote connect COM<N> fs cp src/www/index.html :/www/index.html
mpremote connect COM<N> fs cp src/boot.py        :boot.py
mpremote connect COM<N> fs cp src/main.py        :main.py   # last
```

First flash only (R21), run once before the `main.py` line. Skip on every later upload:

```bash
mpremote connect COM<N> fs cp config.default.json :config.json
```

- `config.default.json` → `:config.json` ONLY on first flash (R21). Re-uploading overwrites the user's live config (incl. `ap_password`). Skip it on firmware updates.
- Never upload `/logs.bin` (device owns it).
- No `logs.html`, `debug.html`, `config.html`: dropped (decision 4).

REPL:

```bash
mpremote connect COM<N> repl
```

---

## 7. Removed in v2 (one note, nothing else)

Removed: BH1750 light sensor (0x23), VBAT ADC on GPIO3, WebREPL, `Button`/`LED` wrapper classes, SSE, debug/logs/config pages, `create_hardware()` / hw dict (R26), `boot_ok` global (R40).

---

## Open — VERIFY ON DEVICE

This is the ONE VERIFY table (R27). Phase docs (04, 05, 06) reference items by V-id. Each item blocks the checkbox it sits next to. Record the result here.

- V1 dropped: CPU fixed 80 MHz, no 40 MHz try.
- [ ] V2 `machine.WDT` keeps counting across `machine.lightsleep`; feed-before-sleep sufficient. Blocks lightsleep only.
- [ ] V3 **V-wake, BLOCKING for lightsleep (R15, R25).** GPIO wake on C3 that fires when EITHER of two active-low buttons goes low. Check per-pin wake, `esp32.gpio_wakeup`, `ext1` ANY_LOW. Fallback if none works: no lightsleep.
- [ ] V4 GPIO wake from `lightsleep` works on C3 with active-low buttons; wake latency acceptable for the 1 s tap rule. Blocks lightsleep only.
- [ ] V5 `ticks_ms` continuous across `lightsleep`.
- [ ] V6 `Pin(…, Pin.OPEN_DRAIN)` available; I2C re-init after bit-bang recovery works (I2C deinit availability).
- [ ] V7 GPIO21 level during ROM boot and before boot.py line 1 (10 kΩ pull-down effect).
- [ ] V8 DS3231 OSF = `0x0F` bit 7; writing `st & 0x7C` clears OSF and keeps EN32kHz/BSY.
- [ ] V9 PCF8574 power-on read-back is 0xFF (brake) — confirm with one raw write/read at first boot.
- [ ] V10 `network.STA_IF` deactivation at boot is needed / harmless.
- [ ] V11 LittleFS `os.rename` replace semantics and `os.stat` size index (06 §3, §7).
- [ ] V12 Settle time 300 ms sufficient for nSLEEP and PCF; compare with 1 s v1 value if motor twitches.
- [ ] V13 **Pin keeps level:** `Pin(21, Pin.OUT)` constructed without `value=` keeps the LOW level set by boot.py.
- [ ] V14 **WiFi at 80 MHz:** CPU fixed 80 MHz at boot (earlier 80 MHz AP-connect panic was IDF heap OOM, fixed by .mpy deploy, R41); confirm `ap_on` starts at that clock. No bump exists.
- [ ] V15 **start_server port:** microdot `app.run` / `start_server` signature and port on MicroPython (R10). Confirm with context7 before implementing; no API from memory.
- [ ] V16 `open(path, "ab")` append works on LittleFS (06 §3).
- [ ] V17 `struct.pack(">HH", ...)` available on MicroPython (06 §2).
- [ ] V18 Power loss mid-append → only torn tail; mid-compaction → old file intact (06 §3). Test build only: pull power during a forced flush loop.
- [ ] V19 Single append write time in ms; far below the 2 s tick (06).
- [ ] V20 Back-EMF during coast (IN1=IN2=LOW) and reversal does not pull nFAULT low (nFAULT stays HIGH; 04 §6). Scope or logic analyser on GPIO20.
- [ ] V21 **LED pin hold during lightsleep:** LED_RED (GPIO6) and PCF P0/P1 keep their level through `machine.lightsleep` (04 §2, §4). Blocks LIGHTSLEEP_OK together with V3/V4.
- [ ] V22 MicroPython runtime for main.py: `__name__ == "__main__"` true; `asyncio.run`, `asyncio.Event().wait()`, `machine.reset()` behave as used in 04 §11.

## Tests (CPython, fake I2C) — `tests/test_hardware.py` (failing first)

`FakeI2C` in the test file: `readfrom_mem(addr, reg, n)` / `writeto_mem` over a register dict; `writeto` records PCF writes. Tests:
1. `test_motor_forward_single_write` — `forward()` → one PCF write, IN1=1, IN2=0.
2. `test_motor_backward_and_stop` — byte values for backward (IN1=0, IN2=1) and stop (both 0), other bits preserved.
3. `test_pcf_led_value_active_low` — `PcfLed(pcf, 0).value(1)` → bit 0 = 0; `value(0)` → bit 0 = 1 (R28).
4. `test_rtc_bcd_roundtrip` — `set_datetime((2026,10,12,6,30,15))` then `datetime()[:6] == (2026,10,12,6,30,15)`.
5. `test_rtc_set_accepts_six_fields` — `set_datetime` with exactly 6 fields works (R22).
6. `test_rtc_set_clears_osf` — status 0x80 before → 0x00 after `set_datetime`; `osf()` False.
7. `test_rtc_osf_keeps_true_time` — status bit7 = 1 → `osf()` True and `datetime()[0] == 2026` (true year, no sentinel) (R17).
8. `test_rtc_forces_24h` — hour register with bit6 set → `set_datetime` writes bit6 = 0.
9. `test_recover_bus_not_in_cpython` — skip marker (MicroPython only).
10. `test_motor_stop_clears_cache_before_write` (R36) — PCF write raises `OSError` inside `stop()` → `pcf.out` IN bits already 0; a following `PcfLed.value(1)` write leaves IN1/IN2 = 0.

Run: `uv run pytest tests/test_hardware.py -v`.
