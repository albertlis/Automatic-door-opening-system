# Hardware Reference — Smart Coop V2

Current design. Items marked **VERIFY ON DEVICE** are not yet measured on hardware (phase 8 checklist).
Removed parts and their replacements: see [Removed from earlier design](#removed-from-earlier-design).

## Bill of Materials

| Component | Part | Qty | Notes |
|-----------|------|-----|-------|
| MCU | ESP32-C3 Super Mini | 1 | USB native, WiFi, 3.3 V |
| Motor driver | DRV8833 module | 1 | 1.5 A peak per channel, nSLEEP power gating |
| DC motor | JGY-370 (6 V, worm gear) | 1 | Self-braking — 0 mA holding current |
| GPIO expander | PCF8574 (I2C 0x20) | 1 | A0=A1=A2=GND. LEDs green/yellow (P0/P1), motor IN1/IN2 (P2/P3) |
| RTC | DS3231 (ZS-042 board) | 1 | TCXO ±2 ppm, CR2032 backup — **modification required** |
| Limit switch | Microswitch (NO) | 2 | TOP (door open) + BOTTOM (door closed), RC filter each |
| Push button | Momentary push-button | 2 | OPEN + CLOSE, RC filter each |
| LED — red | 3 mm LED | 1 | GPIO6 direct. Closed / SAFETY_STOP / ERROR |
| LED — green | 3 mm LED | 1 | PCF8574 P0. Open / moving |
| LED — yellow | 3 mm LED | 1 | PCF8574 P1. RTC not synced / OSF flag |
| Resistor 10 kΩ | 1/4 W | 1 | nSLEEP pull-down (GPIO21 → GND) |
| Resistor 10 kΩ | 1/4 W | 4 | Series, one per input: LIMIT_TOP, LIMIT_BOTTOM, BTN_OPEN, BTN_CLOSE |
| Capacitor 100 nF | ceramic | 4 | RC filter, one per input (MCU pin → GND) |
| LED resistors | ≥ 3.3 kΩ | 3 | Size for < 1 mA at 3.3 V |
| Backup battery | CR2032 | 1 | DS3231 backup — non-rechargeable. Replace preventively every ~3 years |
| Capacitor | 1000 µF electrolytic | 1 | VMOT decoupling for JGY-370 inrush |
| Power supply | 5–6 V DC adapter | 1 | Powers everything via star topology |

---

## Power Topology

Star topology — single PSU, no regulators in signal path:

```
Power Supply 5–6 V DC
├── VMOT ──────────────────── DRV8833 (motor power)
│   └── 1000 µF capacitor to GND  ← JGY-370 inrush protection
│
├── 5 V / USB ─────────────── ESP32-C3 (via USB pin or 5 V pad)
│   └── 3V3 pin ────────────┬── DS3231 VCC
│                           └── PCF8574 VCC
│
└── GND ──────────────────── Common ground (all modules)
```

> **Never power the motor through ESP32 GPIO pins.**
> Motor current spikes will destroy the MCU.

---

## ESP32-C3 Super Mini — Pin Assignments

| Signal | GPIO | Direction | Note |
|--------|------|-----------|------|
| I2C SDA | 4 | Bidirectional | PCF8574 + DS3231 on one bus |
| I2C SCL | 5 | Output | |
| LIMIT_TOP | 0 | Input, pull-up + RC | LOW = door fully open |
| LIMIT_BOTTOM | 1 | Input, pull-up + RC | LOW = door fully closed |
| (unused) | 3 | — | Former battery ADC input, wire removed — see [Removed](#removed-from-earlier-design) |
| LED_RED | 6 | Output | Active-high, direct GPIO. Works even when I2C/PCF8574 is dead |
| BTN_OPEN | 7 | Input, pull-up + RC | LOW = pressed |
| BTN_CLOSE | 10 | Input, pull-up + RC | LOW = pressed |
| DRV8833 nFAULT | 20 | Input | Active-low — LOW = overcurrent / thermal shutdown |
| DRV8833 nSLEEP | 21 | Output | LOW = sleep (~2 µA), HIGH = active. 10 kΩ pull-down to GND. UART0 TX pin: toggles during ROM boot |
| USB D− | 18 | — | USB native, leave unconnected or use freely |
| USB D+ | 19 | — | USB native, leave unconnected or use freely |

### Strapping Pins — Do Not Use

GPIO **2**, **8**, **9** control boot mode on ESP32-C3. Leave unconnected or pull to correct level.
Any load on these pins during reset can prevent boot.

### nSLEEP Pull-Down (GPIO21) — Required

GPIO21 is the UART0 TX pin and toggles while the ROM bootloader runs. Without a pull-down the DRV8833 can wake
during reset. A **10 kΩ pull-down to GND** keeps the driver asleep through reset and ROM boot.

### Input Filtering (RC) — Required

Applies to LIMIT_TOP (GPIO0), LIMIT_BOTTOM (GPIO1), BTN_OPEN (GPIO7), BTN_CLOSE (GPIO10).

```
Switch ──┬── 10 kΩ ──┬── MCU pin   (internal pull-up, active LOW)
         │           │
       GND         100 nF
                     │
                    GND
```

- τ = 10 kΩ × 100 nF = 1 ms — removes switch-bounce spikes and EMI pickup on long cable runs.
- Software debounce (≈ 50 ms, press and release) sits on top of the RC filter.
- Lesson from v1: unfiltered switch lines produced false triggers.

---

## PCF8574 (I2C 0x20) — Pin Assignments

PCF8574 address: A0=A1=A2 tied to GND → `0x20`.
LEDs are **active-low**: pin LOW → LED ON.

| Signal | PCF Pin | Active Level | Notes |
|--------|---------|-------------|-------|
| LED_GREEN | P0 | LOW | Anode → resistor → 3.3 V; cathode → P0 |
| LED_YELLOW | P1 | LOW | Same wiring as LED_GREEN |
| Motor IN1 | P2 | HIGH | DRV8833 input A1 |
| Motor IN2 | P3 | HIGH | DRV8833 input A2 |
| — | P4–P7 | — | Unused, written HIGH |

### Power-On State — Boot Write

PCF8574 outputs power up **HIGH**. With P2 = P3 = HIGH the DRV8833 is in **BRAKE** (IN1 = IN2 = HIGH),
not coast.

Boot sequence (firmware, phase 8):
1. `Pin(21, Pin.OUT, value=0)` — nSLEEP LOW, first line of `boot.py`.
2. Short settle (≈ 100–500 ms).
3. Single PCF8574 write `0xF3`: P0/P1 HIGH (LEDs off), P2/P3 LOW (coast), P4–P7 HIGH.

The nSLEEP pull-down keeps the driver asleep during steps 1–3 in any case.

---

## I2C Bus — Device Addresses

| Device | Address | Notes |
|--------|---------|-------|
| PCF8574 | `0x20` | A0=A1=A2=GND |
| DS3231 | `0x68` | Fixed address |

Both devices on one I2C bus (SDA=GPIO4, SCL=GPIO5, 400 kHz).

Missing device behaviour (firmware, phase 8):
- No blocking scan loop in boot. Boot continues to `main`.
- Bus recovery: 9 SCL clocks, then retry.
- Still failing → controller enters `ERROR` (LED_RED blinks 4 Hz). Buttons and AP remain usable.

---

## DS3231 (ZS-042) — Modification (required)

> ⚠️ **CRITICAL — Do this before installing CR2032.**
>
> The ZS-042 board includes a charging path designed for rechargeable batteries.
> CR2032 is **non-rechargeable**. Left in place, the charging path will try to charge the cell —
> **overheating and fire risk**.

**Must remove:**

1. **Charging resistor** (~200 Ω, in the VBAT charging path, near the battery holder). Mandatory.
2. **Charging diode** in the same path, if present on your board (listed in the project `CLAUDE.md`).
   Check the board with a multimeter before fitting the cell — layouts differ between ZS-042 revisions.

**Optional but recommended:**

3. **Power LED and its series resistor** (~1 mA continuous draw from the backup cell). Removing them
   cuts standby drain.

After modification: insert CR2032. Backup current — **VERIFY ON DEVICE** (multimeter, backup mode).

### Time Keeping

- DS3231 stores **CET (UTC+1)** always. Never adjusted for daylight saving time.
- DST offset (+60 min) is applied in the logic layer only.
- Accuracy: TCXO ±2 ppm ≈ 1 min/year. No drift correction in software.
- Calibration knob if ever needed: aging offset register `0x10`.

### OSF Flag (Oscillator Stop Flag)

- Status register `0x0F`, bit 7.
- `OSF = 1` → oscillator stopped (e.g. backup cell dead while unpowered) → time untrusted.
- Firmware treats OSF = 1 as **RTC not synced**: no automatic moves, yellow LED blinks 1 Hz, web UI asks for time sync.
- `set_datetime()` clears OSF and forces 24 h mode (clears hour-mode bit 6).

### Backup Battery Maintenance

- Replace CR2032 preventively about every 3 years, regardless of OSF state.

---

## DRV8833 Motor Driver

### Power Management

| nSLEEP state | Current draw | When |
|-------------|-------------|------|
| LOW (sleep) | ~2 µA | Motor idle — default between moves |
| HIGH (active) | ~3 mA | 1 ms before motor start, during movement |

nSLEEP is raised 1 ms before enabling motor outputs to allow the driver to wake from sleep.

### Fault Detection

**nFAULT** (GPIO20) goes LOW on:
- Overcurrent (stall current exceeds trip threshold)
- Thermal shutdown

The firmware polls nFAULT in the movement loop every 20 ms. A LOW reading immediately triggers `SAFETY_STOP` — no waiting for the 21-second timeout.

### Current Limit

DRV8833 peak output: **1.5 A per channel**.
> Before final assembly, measure JGY-370 stall current at your supply voltage.
> If stall current > 1.5 A, use both DRV8833 channels in parallel (~3 A peak) or select a different driver.

### Motor Control Truth Table

| IN1 (P2) | IN2 (P3) | Output | Use |
|----------|----------|--------|-----|
| HIGH | LOW | Forward | Open door |
| LOW | HIGH | Reverse | Close door |
| LOW | LOW | Coast (outputs off) | Stop — default idle state |
| HIGH | HIGH | **Brake** | Never written by firmware. Power-on PCF8574 state — see boot write |

### Switching Rules

- Coast → forward: set IN2=LOW **before** IN1=HIGH (avoids a momentary brake state).
- Reversal: coast for ≈ 200 ms (`motor.stop()` + sleep) before driving the opposite direction.
- Stop (every path, incl. limit stop): nSLEEP LOW first (GPIO kill switch), then IN1=IN2=LOW via PCF8574 (best effort).

---

## JGY-370 DC Motor (Worm Gear)

| Property | Value |
|----------|-------|
| Nominal voltage | 6 V |
| Gear type | Worm gear (self-braking) |
| Holding current | **0 mA** — worm gear prevents back-drive |
| Safety timeout | 21 s (configurable via `safety.move_timeout_s`) |

The self-braking property removes the need to keep the motor energised to hold the door in position.
The v1 MG995 servo drew continuous stall current (~400–600 mA) for the same job.

---

## Power Budget

> All values below are **estimates — measure** with a multimeter during the phase 8 checklist.
> Power saving is a project goal, not a survival requirement (PSU is mains-powered, 5–6 V).

| State | Estimate | Basis |
|-------|----------|-------|
| Driver asleep (nSLEEP LOW) | ~2 µA | DRV8833 figure above — measure |
| Driver active, moving | ~3 mA + motor current | Motor current: measure stall current (see above) |
| MCU idle, asyncio loop, 40 MHz | estimate ~10–30 mA | estimate, measure |
| MCU idle, lightsleep cycles (≈ 2 s) | estimate few mA average | estimate, measure. Wake: timer + either button (BTN_OPEN or BTN_CLOSE), VERIFY ON DEVICE |
| AP session (WiFi, 80 MHz) | estimate ~100 mA+ | estimate, measure. AP is on-demand, plus once per boot while RTC unsynced / OSF = 1 |
| DS3231 backup (CR2032) | µA range | VERIFY ON DEVICE |

Lightsleep only when ALL hold: state is `IDLE_OPEN` / `IDLE_CLOSED` / `MANUAL_HOLD_OPEN` / `MANUAL_HOLD_CLOSED`, RTC synced, AP off, no button pressed. Never in `INIT`, `MOVING_*`, `SAFETY_STOP`, `ERROR` (LEDs must keep blinking). Movement and AP use normal asyncio.

Wake source: **either** button. Owner note: an all-low wake mode fires only when **both** pins are low, so it does not fit. **VERIFY ON DEVICE** which C3 MicroPython API gives any-low GPIO wake. No working API → no lightsleep (asyncio sleep fallback). Lightsleep ships only after this is verified.

Exact lightsleep API, `ticks_ms` continuity across lightsleep, and 40 MHz acceptance: **VERIFY ON DEVICE**.

---

## LED Reference

| State | LED | Pattern |
|-------|-----|---------|
| IDLE_OPEN / MANUAL_HOLD_OPEN | Green | Solid |
| IDLE_CLOSED / MANUAL_HOLD_CLOSED | Red | Solid |
| MOVING_OPEN / MOVING_CLOSE | Green | 1 Hz blink |
| SAFETY_STOP | Red | 1 Hz blink |
| ERROR | Red | 4 Hz blink |
| RTC not synced / OSF = 1 | Yellow | 1 Hz blink (any state) |

---

## Electrical Environment

- **Enclosure:** Indoor, controller box placed inside the building (5–25 °C, dry).
- **Wiring:** Limit switches and buttons are the only off-board signal lines. Keep runs short; RC filter at the MCU pin is mandatory.
- **LED series resistors:** Size for < 1 mA at 3.3 V to minimise standby power (≥ 3.3 kΩ for typical LEDs).

---

## Removed from Earlier Design

| Removed | Was | Replacement |
|---------|-----|-------------|
| BH1750 light sensor (GY-302, I2C 0x23) | Ambient light input, 5-sample unanimity | None. Open/close is time-based: sunrise and civil dusk from configured lat/lon |
| VBAT → GPIO3 ADC wire | Battery voltage sense on DS3231 VBAT pad | Wire disconnected: current drained from the cell through the GPIO ESD diode. OSF flag replaces it |
| Yellow LED = low battery | Battery warning | Yellow LED = RTC not synced / OSF |
| Light sensor positioning | Sensor placement rules | Not applicable |
| DS1307 drift correction | v1 nightly correction | Not needed — DS3231 |
