# Hardware Reference — Smart Coop V2

## Bill of Materials

| Component | Part | Qty | Notes |
|-----------|------|-----|-------|
| MCU | ESP32-C3 Super Mini | 1 | USB native, WiFi, 3.3 V |
| Motor driver | DRV8833 module | 1 | 1.5 A peak per channel, sleep mode |
| DC motor | JGY-370 (6 V, worm gear) | 1 | Self-braking — 0 mA holding current |
| GPIO expander | PCF8574 (I2C, 0x20) | 1 | A0=A1=A2=GND |
| RTC | DS3231 (ZS-042 board) | 1 | TCXO ±2 ppm, CR2032 backup — **see modification** |
| Light sensor | BH1750 (GY-302) | 1 | I2C 0x23, 3.3 V compatible |
| Limit switch | Microswitch (NO) | 2 | TOP (door open) + BOTTOM (door closed) |
| Push button | Momentary push-button | 2 | OPEN + CLOSE manual control |
| LED — red | 3 mm LED | 1 | Safety stop / closed indicator |
| LED — yellow | 3 mm LED | 1 | Low RTC battery indicator |
| LED — green | 3 mm LED | 1 | Open / moving indicator |
| Backup battery | CR2032 | 1 | DS3231 RTC backup — non-rechargeable |
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
│   └── 3V3 pin ────────────┬── BH1750 VCC
│                           ├── DS3231 VCC
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
| I2C SDA | 4 | Bidirectional | BH1750 + DS3231 + PCF8574 on one bus |
| I2C SCL | 5 | Output | |
| LIMIT_TOP | 0 | Input, pull-up | LOW = door fully open |
| LIMIT_BOTTOM | 1 | Input, pull-up | LOW = door fully closed |
| VBAT_SENSE | 3 | ADC input | ADC1_CH3, direct to DS3231 ZS-042 VBAT pad, max 3.6 V |
| LED_RED | 6 | Output | Active-high, direct GPIO. Works even when I2C is dead |
| BTN_OPEN | 7 | Input, pull-up | LOW = pressed |
| BTN_CLOSE | 10 | Input, pull-up | LOW = pressed |
| DRV8833 nFAULT | 20 | Input | Active-low — LOW = overcurrent / thermal shutdown |
| DRV8833 nSLEEP | 21 | Output | LOW = sleep (~2 µA), HIGH = active |
| USB D− | 18 | — | USB native, leave unconnected or use freely |
| USB D+ | 19 | — | USB native, leave unconnected or use freely |

### Strapping Pins — Do Not Use

GPIO **2**, **8**, **9** control boot mode on ESP32-C3. Leave unconnected or pull to correct level.  
Any load on these pins during reset can prevent boot.

---

## PCF8574 (I2C 0x20) — Pin Assignments

PCF8574 address: A0=A1=A2 tied to GND → `0x20`.  
LEDs are **active-low**: pin LOW → LED ON. Initial state: all pins HIGH (LEDs off, motor coast).

| Signal | PCF Pin | Active Level | Notes |
|--------|---------|-------------|-------|
| LED_GREEN | P0 | LOW | Anode → resistor → 3.3 V; cathode → P0 |
| LED_YELLOW | P1 | LOW | Same wiring as LED_GREEN |
| Motor IN1 | P2 | HIGH | DRV8833 input A1 |
| Motor IN2 | P3 | HIGH | DRV8833 input A2 |
| — | P4–P7 | — | Unused, leave floating or pull HIGH |

---

## I2C Bus — Device Addresses

| Device | Address | Notes |
|--------|---------|-------|
| PCF8574 | `0x20` | A0=A1=A2=GND |
| BH1750 | `0x23` | ADDR pin LOW |
| DS3231 | `0x68` | Fixed address |

All three devices on one I2C bus (SDA=GPIO4, SCL=GPIO5, 400 kHz).  
`boot.py` scans the bus on startup and blinks LED_RED at 10 Hz if any device is missing.

---

## DS3231 (ZS-042) — Required Modification

> ⚠️ **CRITICAL — Do this before installing CR2032.**
>
> The ZS-042 board includes a charging circuit designed for rechargeable batteries.  
> CR2032 is **non-rechargeable**. Without modification, the charging circuit will attempt  
> to charge the battery, causing **overheating and potential fire**.

**Desolder both components:**

1. **Power LED** — the small LED and its series resistor on the board (draws ~1 mA continuously)
2. **Charging resistor** — the resistor in the VBAT charging path (often marked 200Ω or similar, near the battery holder)

After modification: insert CR2032. DS3231 will draw <1 µA from the battery in backup mode.

**DS3231 time storage convention in this project:**  
The RTC always stores **CET (UTC+1)**. The clock is never adjusted for daylight saving time.  
The DST offset (+60 min) is applied in the application logic layer only.

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

The firmware polls nFAULT in the motor movement loop every 20 ms. A LOW reading immediately triggers `SAFETY_STOP` — no waiting for the 21-second timeout.

### Current Limit

DRV8833 peak output: **1.5 A per channel**.  
> Before final assembly, measure JGY-370 stall current at your supply voltage.  
> If stall current > 1.5 A, use both DRV8833 channels in parallel (~3 A peak) or select a different driver.

### Motor Control Truth Table

| IN1 (P2) | IN2 (P3) | Motor action | Direction |
|----------|----------|-------------|-----------|
| HIGH | LOW | Forward (coast stop) | Open door |
| LOW | HIGH | Backward (coast stop) | Close door |
| LOW | LOW | Coast (free spin) | Stop — default |
| HIGH | HIGH | Brake | Not used |

> **Wiring note:** When transitioning from coast to forward, set IN2=LOW **before** IN1=HIGH  
> to avoid a momentary brake state.

---

## JGY-370 DC Motor (Worm Gear)

| Property | Value |
|----------|-------|
| Nominal voltage | 6 V |
| Gear type | Worm gear (self-braking) |
| Holding current | **0 mA** — worm gear prevents back-drive |
| Safety timeout | 21 s (configurable via `safety.move_timeout_s`) |

The self-braking property eliminates the need to hold the motor energised to keep the door in position — a key improvement over the v1 MG995 servo, which drew continuous stall current (~400–600 mA) to hold the door open.

---

## Electrical Environment

- **Enclosure:** Indoor, controller box placed inside the building (5–25 °C, dry).
- **Light sensor:** Must be positioned to see only outdoor light. Box interior must be sealed against light leakage.
- **LED series resistors:** Size for < 1 mA at 3.3 V to minimise standby power (≥ 3.3 kΩ for typical LEDs).
