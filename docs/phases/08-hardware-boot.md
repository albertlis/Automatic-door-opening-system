# Phase 8 — Hardware Layer + Boot

## Goal

Replace mock objects with real MicroPython hardware drivers. Write `boot.py`. Verify the complete system on physical ESP32-C3 hardware using the §14 checklist.

## Prerequisites

- All phases 1–7 complete and green
- Physical hardware assembled: ESP32-C3 Super Mini + PCF8574 + DS3231 + BH1750 + DRV8833 + JGY-370
- Modifications done: DS3231 ZS-042 charging circuit removed (diode + resistor desoldered)
- MicroPython flashed on ESP32-C3

## Deliverables

| File | Status |
|------|--------|
| `src/hardware.py` | create |
| `src/boot.py` | create |
| `src/main.py` | verify (real hardware path in `_create_hardware_ctrl`) |

No new automated tests — hardware drivers require physical I2C devices. Verify with §14 hardware checklist.

---

## Flash MicroPython

```bash
# Erase flash
esptool --chip esp32c3 --port COM<N> erase_flash

# Flash MicroPython for ESP32-C3 (download from micropython.org/download/ESP32_GENERIC_C3/)
esptool --chip esp32c3 --port COM<N> --baud 460800 write_flash -z 0x0 ESP32_GENERIC_C3-*.bin
```

---

## Upload Files

Upload order matters — `main.py` last (triggers boot on upload):

```bash
mpremote connect COM<N> fs mkdir /www

mpremote connect COM<N> fs cp src/compat.py :compat.py
mpremote connect COM<N> fs cp src/astro.py :astro.py
mpremote connect COM<N> fs cp src/config.py :config.py
mpremote connect COM<N> fs cp config.default.json :config.json
mpremote connect COM<N> fs cp src/logs.py :logs.py
mpremote connect COM<N> fs cp src/state.py :state.py
mpremote connect COM<N> fs cp src/hardware.py :hardware.py
mpremote connect COM<N> fs cp src/web.py :web.py
mpremote connect COM<N> fs cp src/www/index.html :/www/index.html
mpremote connect COM<N> fs cp src/www/config.html :/www/config.html
mpremote connect COM<N> fs cp src/www/logs.html :/www/logs.html
mpremote connect COM<N> fs cp src/www/debug.html :/www/debug.html
mpremote connect COM<N> fs cp src/boot.py :boot.py
mpremote connect COM<N> fs cp src/main.py :main.py   # last — triggers boot
```

---

## 1. `src/hardware.py`

MicroPython-only. Never imported on CPython (mocks used instead).

### `PCF8574` (§11 exact)

```python
from machine import I2C

class PCF8574:
    def __init__(self, i2c: I2C, addr: int = 0x20):
        self._i2c = i2c
        self._addr = addr
        self._out = 0xFF   # all HIGH: LEDs off, inputs float high

    def _write(self) -> None:
        self._i2c.writeto(self._addr, bytes([self._out]))

    def set_pin(self, pin: int, val: int) -> None:
        if val:
            self._out |= (1 << pin)
        else:
            self._out &= ~(1 << pin)
        self._write()

    def get_pin(self, pin: int) -> bool:
        return bool(self._i2c.readfrom(self._addr, 1)[0] & (1 << pin))

    def read_all(self) -> int:
        return self._i2c.readfrom(self._addr, 1)[0]
```

### `Motor` (DRV8833 via PCF8574)

PCF pin assignments: IN1=P2, IN2=P3.

```python
class Motor:
    def __init__(self, pcf: PCF8574, in1: int = 2, in2: int = 3):
        self._pcf = pcf
        self._in1 = in1
        self._in2 = in2
        self.stop()   # ensure coast on init

    def forward(self) -> None:    # open: IN1=HIGH, IN2=LOW
        self._pcf.set_pin(self._in2, 0)
        self._pcf.set_pin(self._in1, 1)

    def backward(self) -> None:   # close: IN1=LOW, IN2=HIGH
        self._pcf.set_pin(self._in1, 0)
        self._pcf.set_pin(self._in2, 1)

    def stop(self) -> None:       # coast: IN1=LOW, IN2=LOW
        self._pcf.set_pin(self._in1, 0)
        self._pcf.set_pin(self._in2, 0)
```

**IMPORTANT:** `forward()` sets IN2=LOW first then IN1=HIGH to avoid momentary brake. Order matters.

### `RTC` (DS3231 via I2C at 0x68)

DS3231 register layout: seconds, minutes, hours, day, date, month, year (all BCD, year is 00–99 offset from 2000).

```python
class RTC:
    ADDR = 0x68

    def __init__(self, i2c: I2C):
        self._i2c = i2c

    @staticmethod
    def _bcd2dec(v: int) -> int:
        return (v >> 4) * 10 + (v & 0x0F)

    @staticmethod
    def _dec2bcd(v: int) -> int:
        return ((v // 10) << 4) | (v % 10)

    def datetime(self) -> tuple:
        buf = self._i2c.readfrom_mem(self.ADDR, 0x00, 7)
        sec   = self._bcd2dec(buf[0] & 0x7F)
        minute = self._bcd2dec(buf[1])
        hour  = self._bcd2dec(buf[2] & 0x3F)
        # buf[3] = day-of-week (ignored)
        date  = self._bcd2dec(buf[4])
        month = self._bcd2dec(buf[5] & 0x1F)
        year  = self._bcd2dec(buf[6]) + 2000
        return (year, month, date, hour, minute, sec, 0, 0)

    def set_datetime(self, dt: tuple) -> None:
        year, month, date, hour, minute, sec, *_ = dt
        buf = bytes([
            self._dec2bcd(sec),
            self._dec2bcd(minute),
            self._dec2bcd(hour),
            1,                          # day-of-week (don't care)
            self._dec2bcd(date),
            self._dec2bcd(month),
            self._dec2bcd(year - 2000),
        ])
        self._i2c.writeto_mem(self.ADDR, 0x00, buf)
```

### `LightSensor` (BH1750 at 0x23)

```python
class LightSensor:
    ADDR = 0x23
    CMD_CONTINUOUS_HIGH_RES = 0x10   # 1 lux resolution, 120ms measurement

    def __init__(self, i2c: I2C):
        self._i2c = i2c
        # Start continuous measurement
        self._i2c.writeto(self.ADDR, bytes([self.CMD_CONTINUOUS_HIGH_RES]))

    def read_lux(self) -> float:
        data = self._i2c.readfrom(self.ADDR, 2)
        raw = (data[0] << 8) | data[1]
        return raw / 1.2   # BH1750 raw → lux
```

### `Button`

```python
from machine import Pin

class Button:
    def __init__(self, pin: Pin):
        self._pin = pin

    def value(self) -> int:
        return self._pin.value()
```

### `LED`

Two variants: direct GPIO (LED_RED on GPIO6) and PCF8574 (LED_GREEN/YELLOW on PCF P0/P1, active-low).

```python
class LED:
    def __init__(self, driver, pin: int, active_low: bool = False):
        """driver: Pin (GPIO) or PCF8574 instance."""
        self._driver = driver
        self._pin = pin
        self._active_low = active_low

    def on(self) -> None:
        self._driver.value(0 if self._active_low else 1) if hasattr(self._driver, "value") \
            else self._driver.set_pin(self._pin, 0 if self._active_low else 1)

    def off(self) -> None:
        self._driver.value(1 if self._active_low else 0) if hasattr(self._driver, "value") \
            else self._driver.set_pin(self._pin, 1 if self._active_low else 0)
```

LED wiring (§2):
- `LED_RED`: GPIO6, direct, active-high (GPIO6=HIGH → LED on)
- `LED_GREEN`: PCF8574 P0, active-low (P0=0 → LED on)
- `LED_YELLOW`: PCF8574 P1, active-low (P1=0 → LED on)

### `VBatADC`

```python
from machine import ADC, Pin

class VBatADC:
    def __init__(self, pin: int = 3):
        self._adc = ADC(Pin(pin), atten=ADC.ATTN_11DB)   # range 0–3.6V

    def read_v(self) -> float:
        return self._adc.read_uv() / 1_000_000
```

---

## 2. `src/boot.py`

```python
import machine

machine.freq(40_000_000)   # 40MHz, low power. WiFi bumps to 80MHz when needed.

import network
network.WLAN(network.AP_IF).active(False)   # WiFi off by default

import webrepl
webrepl.start(password="coop")

from machine import I2C, Pin
i2c = I2C(0, sda=Pin(4), scl=Pin(5), freq=400_000)

# Scan I2C bus — assert expected devices
devices = i2c.scan()
EXPECTED = {0x20, 0x23, 0x68}   # PCF8574, BH1750, DS3231
missing = EXPECTED - set(devices)
if missing:
    # Blink LED_RED fast to signal I2C failure (can't use PCF8574 if PCF missing)
    led_red = Pin(6, Pin.OUT)
    import time
    while True:
        led_red.value(1)
        time.sleep_ms(100)
        led_red.value(0)
        time.sleep_ms(100)

from src.hardware import PCF8574
pcf = PCF8574(i2c, 0x20)
pcf.set_pin(0, 1)   # LED_GREEN off
pcf.set_pin(1, 1)   # LED_YELLOW off
pcf.set_pin(2, 0)   # Motor IN1 LOW (coast)
pcf.set_pin(3, 0)   # Motor IN2 LOW (coast)

from src.logs import init_log
init_log()

# Boot complete — main.py runs next (MicroPython executes boot.py then main.py)
```

---

## 3. Update `src/main.py` — Real Hardware Path

The `_create_hardware_ctrl()` function in Phase 4 already has the ESP32 branch. Verify pin assignments match §2:

```python
# ESP32-C3 Super Mini pin assignments (§2)
# I2C: SDA=GPIO4, SCL=GPIO5
# LIMIT_TOP: GPIO0 (INPUT_PULLUP)
# LIMIT_BOTTOM: GPIO1 (INPUT_PULLUP)
# LED_RED: GPIO6 (OUTPUT, active-high, direct)
# BTN_OPEN: GPIO7 (INPUT_PULLUP)
# BTN_CLOSE: GPIO10 (INPUT_PULLUP)
# nFAULT: GPIO20 (INPUT, active-low)
# nSLEEP: GPIO21 (OUTPUT)
# PCF8574:
#   P0 = LED_GREEN (active-low)
#   P1 = LED_YELLOW (active-low)
#   P2 = Motor IN1
#   P3 = Motor IN2

from machine import I2C, Pin
i2c = I2C(0, sda=Pin(4), scl=Pin(5), freq=400_000)
pcf = PCF8574(i2c, 0x20)

return CoopController(
    motor=Motor(pcf, in1=2, in2=3),
    rtc=RTC(i2c),
    light_sensor=LightSensor(i2c),
    pcf=pcf,
    limit_top=Button(Pin(0, Pin.IN, Pin.PULL_UP)),
    limit_bottom=Button(Pin(1, Pin.IN, Pin.PULL_UP)),
    btn_open=Button(Pin(7, Pin.IN, Pin.PULL_UP)),
    btn_close=Button(Pin(10, Pin.IN, Pin.PULL_UP)),
    leds=(
        LED(Pin(6, Pin.OUT), pin=6, active_low=False),          # red: GPIO6 direct
        LED(pcf, pin=1, active_low=True),                        # yellow: PCF P1
        LED(pcf, pin=0, active_low=True),                        # green: PCF P0
    ),
    nsleep=Pin(21, Pin.OUT),
    nfault=Pin(20, Pin.IN),
    config=cfg,
)
```

Also update `start_ap_session()` to use correct path for HTML files (`/www/index.html`):

```python
# In web.py, update _html_path() for ESP32
# /www/ is the LittleFS path; src/www/ is local dev path
```

---

## 4. VBAT Monitoring — Wire to `control_loop`

Add to `main.py` `control_loop()` (after the midnight check):

```python
# VBAT check (every 2s, same as control loop)
import sys
if sys.implementation.name == "micropython":
    from src.hardware import VBatADC
    _vbat_adc = VBatADC(pin=3)
    vbat = _vbat_adc.read_v()
    ctrl._vbat_low = vbat < 2.7
    # Also update status JSON
    ctrl._vbat_v = vbat
```

Add `_vbat_v` field to `CoopController` (initialized to `None`). Update `status_json()` to include it.

---

## 5. Hardware Checklist (§14)

After flashing, verify each item:

### I2C Bus

- [ ] `pcf_scan = i2c.scan()` → contains `0x20` (PCF8574), `0x23` (BH1750), `0x68` (DS3231)
- [ ] `boot.py` does not hang or blink error pattern

### Limit Switches

- [ ] Manually actuate LIMIT_TOP → `/api/status` shows `limit_top: true`
- [ ] Manually actuate LIMIT_BOTTOM → `/api/status` shows `limit_bottom: true`
- [ ] Both inactive → state machine stays in INIT until time-based decision

### Light Sensor Test (5-sample unanimity)

- [ ] Point flashlight at BH1750 for >1.5s → `lux_ready=True`, all 5 samples > `lux_open` → `MOVING_OPEN`
- [ ] Cover sensor for >1.5s (outside time window) → all 5 < `lux_close` → `MOVING_CLOSE`
- [ ] Partial cover (3/5 below threshold) → no state change

### Hysteresis

- [ ] Hold lux at ~3.5 lx (between `lux_close=3.0` and `lux_open=8.0`) → no oscillation

### Absolute Close Override

- [ ] Set RTC to 21:59:55 via `/api/time` with appropriate timestamp → wait for 22:00 → closes regardless of lux

### Safety Stop

- [ ] Mechanically block motor mid-travel → motor current stalls → after `move_timeout_s` (21s) → `SAFETY_STOP`
- [ ] LED_RED blinks 1Hz in SAFETY_STOP state
- [ ] Physical button press → `MOVING_OPEN/CLOSE` (RECOVERY trigger) → reaches limit → `IDLE_*`

### nFAULT Test

- [ ] Briefly short nFAULT (GPIO20) to GND → `SAFETY_STOP` immediately (before timeout)

### LED Signals

| State | Expected |
|-------|---------|
| `IDLE_OPEN` / `MANUAL_HOLD_OPEN` | Green solid |
| `IDLE_CLOSED` / `MANUAL_HOLD_CLOSED` | Red solid |
| `MOVING_OPEN` / `MOVING_CLOSE` | Green 1Hz blink |
| `SAFETY_STOP` | Red 1Hz blink |
| `ERROR` | Red 4Hz blink |
| Low battery | Yellow 1Hz blink (overlays other LEDs) |

### Web UI

- [ ] Double-click BTN_OPEN or BTN_CLOSE → WiFi `Coop_Control` appears
- [ ] Connect phone → open `http://192.168.4.1`
- [ ] Status page shows live state via SSE (no refresh needed)
- [ ] Config form saves and persists after reboot
- [ ] Time sync button sets DS3231 correctly (check time on debug page)
- [ ] AP auto-shuts off after 10 minutes of inactivity

### WebREPL

- [ ] Connect via `http://micropython.org/webrepl/#192.168.4.1:8266` with password `coop`
- [ ] Can execute `ctrl.state` in REPL and see current state
- [ ] Can upload files via WebREPL

### Persistence

- [ ] Change config via web UI → physically power-cycle → config survives
- [ ] Log records visible on `/logs` page after several open/close cycles
- [ ] DS3231 holds correct time after unplugging power for 1 minute (CR2032 backup)

### VBAT

- [ ] `/api/status` shows `vbat_v ≈ 3.0` for a fresh CR2032
- [ ] Simulate low battery: mock `ctrl._vbat_low = True` via WebREPL → LED_YELLOW blinks

### DRV8833 Power Management

- [ ] Measure nSLEEP (GPIO21): LOW when motor idle → ~2µA quiescent
- [ ] During move: nSLEEP goes HIGH 1ms before motor starts

---

## Notes

- DS3231 ZS-042 **must** have charging circuit removed before installing CR2032. Non-rechargeable battery + charging circuit = fire hazard.
- GPIO2, GPIO8, GPIO9 are strapping pins on ESP32-C3 — leave unconnected or pull correctly. Do not use.
- GPIO18/19 are USB D-/D+ — safe to leave unconnected, USB still works.
- `i2c.scan()` returns addresses as integers. PCF8574 with A0=A1=A2=GND → `0x20`. BH1750 ADDR pin low → `0x23`. DS3231 → `0x68`.
- `PCF8574._out` starts at `0xFF` (all HIGH). On first write, it sets LEDs off and motor coast simultaneously. Verify no spurious motor movement on boot.
- `machine.freq(80_000_000)` required for WiFi. `freq(40_000_000)` for normal operation. AP session wrapper in `start_ap_session()` handles the transition.
- MicroPython `"r+b"` mode: if unavailable in LittleFS, implement `write_record()` as read-all → modify → write-all. Acceptable for a 2924-byte file.
- After first flash and first-boot AP session: set time via web UI, verify timezone (CET = UTC+1 stored always), then normal operation begins.
