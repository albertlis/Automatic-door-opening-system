# hardware.py — MicroPython drivers. CPython import is allowed only for tests (fake I2C injected).
try:
    from machine import I2C, Pin
except ImportError:  # CPython tests
    I2C = Pin = None
try:
    import network
except ImportError:  # CPython tests
    network = None
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
    sda.value(1)  # STOP: SDA rises while SCL is high
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
        self.out = 0xFF  # chip power-on value (V9 read-back at boot)

    def write(self, value):
        value &= 0xFF
        self._i2c.writeto(self._addr, bytes((value,)))
        self.out = value  # cache only after successful write

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

    def forward(self):  # open: IN1=1, IN2=0
        self._drive(1, 0)

    def backward(self):  # close: IN1=0, IN2=1
        self._drive(0, 1)

    def stop(self):  # coast: IN1=0, IN2=0 (worm gear self-brakes)
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
        """(y, mo, d, h, mi, s, wd), wd Mon=0 (DS3231 reg 1..7 minus 1).
        24h mode assumed: set_datetime() always forces it."""
        b = self._i2c.readfrom_mem(self.ADDR, 0x00, 7)
        return (
            bcd2dec(b[6]) + 2000,
            bcd2dec(b[5] & 0x1F),
            bcd2dec(b[4]),
            bcd2dec(b[2] & 0x3F),
            bcd2dec(b[1]),
            bcd2dec(b[0] & 0x7F),
            (b[3] & 0x07) - 1,  # reg 1..7 (Mon=1) -> wd Mon=0 (R22, 05 §2.3)
        )

    def osf(self):
        """Status 0x0F bit 7. True = time untrusted -> state treats RTC as unsynced."""
        return bool(self._i2c.readfrom_mem(self.ADDR, 0x0F, 1)[0] & 0x80)

    def set_datetime(self, dt):
        """dt: >= 6 fields (y, mo, d, h, mi, s[, wd]). Clears OSF, forces 24h mode."""
        y, mo, d, h, mi, s = dt[:6]
        wd = dt[6] + 1 if len(dt) > 6 else 1  # wd Mon=0 -> DS3231 reg Mon=1..Sun=7
        buf = bytes(
            (
                dec2bcd(s) & 0x7F,  # CH=0: oscillator running
                dec2bcd(mi),
                dec2bcd(h) & 0x3F,  # bit6=0: 24h mode
                wd,  # day-of-week reg 1..7 (Mon=1), from wd Mon=0
                dec2bcd(d),
                dec2bcd(mo),
                dec2bcd(y - 2000),
            )
        )
        self._i2c.writeto_mem(self.ADDR, 0x00, buf)
        st = self._i2c.readfrom_mem(self.ADDR, 0x0F, 1)[0]
        # clear OSF (bit7) and alarm flags A1F/A2F (bits 0,1, write-0-to-clear). Keep bits 2..6.
        self._i2c.writeto_mem(self.ADDR, 0x0F, bytes((st & 0x7C,)))


# AP for ap_session (04 §10): module is passed as the `wifi` DI object (ap_on / ap_off).
def ap_on(ssid, password):
    """password "" = open AP (R6), else WPA2 with that key (8..63 chars, validated in config)."""
    ap = network.WLAN(network.AP_IF)  # VERIFY (08 V23): network.AP_IF / WLAN constant names
    ap.active(True)
    if password:
        ap.config(ssid=ssid, key=password, security=network.WLAN.SEC_WPA2)  # V23 checked on MicroPython 1.29.0 C3
    else:
        ap.config(ssid=ssid, security=network.WLAN.SEC_OPEN)  # explicit: driver keeps last security (V24, MP 1.29 C3)


def ap_off():
    network.WLAN(network.AP_IF).active(False)
