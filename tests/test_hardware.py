"""CPython tests for src/hardware.py and src/boot.py.

FakeI2C stands in for machine.I2C. boot.py runs with fake machine/time/network/
hardware/logs modules injected into sys.modules. recover_bus() is MicroPython-only.
"""

import runpy
import sys
import types
from pathlib import Path

import pytest

import hardware
from hardware import PCF8574, RTC, Motor, PcfLed, bcd2dec, dec2bcd

BOOT_PATH = Path(__file__).resolve().parents[1] / "src" / "boot.py"
PCF_ADDR = 0x20
RTC_ADDR = 0x68


class FakeI2C:
    """DS3231 register file at 0x68. PCF8574 writes at 0x20 logged in pcf_writes."""

    def __init__(self, rtc_status=0x00, pcf_fail=0, scan=(0x20, 0x68)):
        self.regs = bytearray(0x10)
        self.regs[0x0F] = rtc_status
        self.pcf_writes = []
        self.pcf_fail = pcf_fail  # next N PCF writes raise OSError
        self._scan = list(scan)

    def scan(self):
        return list(self._scan)

    def readfrom_mem(self, addr, reg, n):
        assert addr == RTC_ADDR
        return bytes(self.regs[reg : reg + n])

    def writeto_mem(self, addr, reg, buf):
        assert addr == RTC_ADDR
        self.regs[reg : reg + len(buf)] = buf

    def writeto(self, addr, buf):
        assert addr == PCF_ADDR
        if self.pcf_fail:
            self.pcf_fail -= 1
            raise OSError(5, "EIO")
        self.pcf_writes.append(bytes(buf))


def test_bcd_helpers_roundtrip():
    for dec in range(100):
        assert bcd2dec(dec2bcd(dec)) == dec
    assert dec2bcd(59) == 0x59
    assert bcd2dec(0x12) == 12


@pytest.mark.parametrize(("method", "expected"), [("forward", 0xF7), ("backward", 0xFB), ("stop", 0xF3)])
def test_motor_one_write_with_expected_byte(method, expected):
    i2c = FakeI2C()
    getattr(Motor(PCF8574(i2c), in1=2, in2=3), method)()
    assert i2c.pcf_writes == [bytes((expected,))]


def test_motor_keeps_led_bits():
    i2c = FakeI2C()
    pcf = PCF8574(i2c)
    PcfLed(pcf, 0).value(1)  # green on: P0 low -> 0xFE
    Motor(pcf).forward()  # clear P2/P3, set P2 -> 0xF6
    assert i2c.pcf_writes[-1] == bytes((0xF6,))


@pytest.mark.parametrize(("bit", "value", "expected"), [(0, 1, 0xFE), (0, 0, 0xFF), (1, 1, 0xFD), (1, 0, 0xFF)])
def test_pcf_led_value_active_low(bit, value, expected):
    i2c = FakeI2C()
    PcfLed(PCF8574(i2c), bit).value(value)
    assert i2c.pcf_writes == [bytes((expected,))]


def test_pcf_failed_write_keeps_old_cache():
    i2c = FakeI2C(pcf_fail=1)
    pcf = PCF8574(i2c)
    with pytest.raises(OSError):
        pcf.write(0xF3)
    assert pcf.out == 0xFF


def test_motor_stop_clears_cache_before_failing_write():
    i2c = FakeI2C()
    pcf = PCF8574(i2c)
    motor = Motor(pcf)
    motor.forward()  # cache 0xF7
    i2c.pcf_fail = 1
    with pytest.raises(OSError):
        motor.stop()
    assert (pcf.out & 0x0C) == 0  # IN bits cleared although write failed
    PcfLed(pcf, 0).value(1)  # later LED write must not re-assert IN1/IN2
    assert i2c.pcf_writes == [bytes((0xF7,)), bytes((0xF2,))]


def test_boot_pcf_byte_0xf3_is_coast_and_leds_off():
    i2c = FakeI2C()
    pcf = PCF8574(i2c)
    pcf.write(0xF3)
    assert i2c.pcf_writes == [bytes((0xF3,))]
    assert pcf.out == 0xF3


@pytest.mark.parametrize("dt", [(2026, 10, 12, 6, 30, 15), [2026, 10, 12, 6, 30, 15]])
def test_rtc_bcd_roundtrip_six_fields(dt):
    rtc = RTC(FakeI2C())
    rtc.set_datetime(dt)
    assert rtc.datetime()[:6] == (2026, 10, 12, 6, 30, 15)


def test_rtc_datetime_masks_ch_and_century_bits():
    i2c = FakeI2C()
    i2c.regs[0:7] = bytes((0x95, 0x30, 0x06, 0x03, 0x12, 0x90, 0x26))  # CH=1 sec 15, century bit on month
    assert RTC(i2c).datetime() == (2026, 10, 12, 6, 30, 15, 2)


@pytest.mark.parametrize("wd", range(7))
def test_rtc_weekday_maps_mon0_to_reg_mon1(wd):
    i2c = FakeI2C()
    rtc = RTC(i2c)
    rtc.set_datetime((2026, 10, 12, 6, 30, 15, wd))
    assert i2c.regs[0x03] == wd + 1
    assert rtc.datetime()[6] == wd


@pytest.mark.parametrize(("before", "after"), [(0x80, 0x00), (0xFF, 0x7C)])
def test_rtc_set_clears_osf_keeps_other_status_bits(before, after):
    i2c = FakeI2C(rtc_status=before)
    rtc = RTC(i2c)
    assert rtc.osf() is True
    rtc.set_datetime((2026, 10, 12, 6, 30, 15))
    assert i2c.regs[0x0F] == after
    assert rtc.osf() is False


def test_rtc_osf_keeps_true_time():
    i2c = FakeI2C()
    rtc = RTC(i2c)
    rtc.set_datetime((2026, 10, 12, 6, 30, 15))
    i2c.regs[0x0F] = 0x80  # OSF raised after power loss
    assert rtc.osf() is True
    assert rtc.datetime()[0] == 2026


def test_rtc_set_writes_24h_hour():
    i2c = FakeI2C()
    RTC(i2c).set_datetime((2026, 10, 12, 18, 30, 15))
    assert i2c.regs[0x02] == 0x18  # bit6 = 0 -> 24h mode


@pytest.mark.skip(reason="MicroPython only: Pin.OPEN_DRAIN and time.sleep_us")
def test_recover_bus_not_in_cpython():
    pass


def run_boot(monkeypatch, i2c, log):
    machine = types.ModuleType("machine")

    class Pin:
        IN, OUT, PULL_UP, OPEN_DRAIN = 0, 1, 2, 3

        def __init__(self, n, mode=None, pull=None, value=None):
            log.append(("pin", n, mode, value))

    machine.Pin = Pin
    machine.freq = lambda hz: log.append(("freq", hz))

    time_mod = types.ModuleType("time")
    time_mod.sleep_ms = lambda ms: log.append(("sleep_ms", ms))

    network = types.ModuleType("network")
    network.AP_IF, network.STA_IF = 0, 1

    class WLAN:
        def __init__(self, iface):
            self.iface = iface

        def active(self, on):
            log.append(("wlan", self.iface, on))

    network.WLAN = WLAN

    hw = types.ModuleType("hardware")
    hw.get_i2c = lambda: i2c
    hw.get_pcf = lambda: PCF8574(i2c)
    hw.recover_bus = lambda: log.append(("recover_bus",))

    logs = types.ModuleType("logs")
    logs.init_log = lambda: log.append(("init_log",))

    fakes = {"machine": machine, "time": time_mod, "network": network, "hardware": hw, "logs": logs}
    for name, mod in fakes.items():
        monkeypatch.setitem(sys.modules, name, mod)
    runpy.run_path(str(BOOT_PATH), run_name="boot")


def test_boot_nsleep_low_is_first_action(monkeypatch):
    log = []
    run_boot(monkeypatch, FakeI2C(), log)
    assert log[0] == ("pin", 21, 1, 0)


def test_boot_sets_fixed_80mhz_once_after_wlan_init(monkeypatch):
    log = []
    run_boot(monkeypatch, FakeI2C(), log)
    freq_calls = [e for e in log if e[0] == "freq"]
    assert freq_calls == [("freq", 80_000_000)]
    assert log.index(("wlan", 1, False)) < log.index(("freq", 80_000_000))


def test_boot_writes_0xf3_and_finishes(monkeypatch):
    i2c = FakeI2C()
    log = []
    run_boot(monkeypatch, i2c, log)
    assert i2c.pcf_writes == [bytes((0xF3,))]
    assert ("recover_bus",) not in log
    assert ("init_log",) in log


def test_boot_missing_device_recovers_once_and_continues(monkeypatch):
    i2c = FakeI2C(scan=(0x68,))
    log = []
    run_boot(monkeypatch, i2c, log)
    assert log.count(("recover_bus",)) == 1
    assert i2c.pcf_writes == [bytes((0xF3,))]
    assert ("init_log",) in log


def test_boot_pcf_write_failure_does_not_stop_boot(monkeypatch):
    i2c = FakeI2C(pcf_fail=1)
    log = []
    run_boot(monkeypatch, i2c, log)
    assert i2c.pcf_writes == []
    assert ("init_log",) in log


def fake_network(monkeypatch):
    """Records WLAN calls. Real module = MicroPython network; here only the AP calls ap_on/ap_off make."""
    log = []

    class WLAN:
        SEC_OPEN, SEC_WPA2 = 0, 3  # values on MicroPython 1.29.0 C3

        def __init__(self, iface):
            self.iface = iface

        def active(self, on):
            log.append(("active", self.iface, on))

        def config(self, **kw):
            log.append(("config", kw))

    monkeypatch.setattr(hardware, "network", types.SimpleNamespace(AP_IF=1, WLAN=WLAN))
    return log


def test_ap_on_open_when_password_empty(monkeypatch):
    log = fake_network(monkeypatch)
    hardware.ap_on("Szczesliwy_Kurczak", "")
    # security must be explicit: driver keeps the previous session's WPA2 otherwise (seen on device)
    assert log == [("active", 1, True), ("config", {"ssid": "Szczesliwy_Kurczak", "security": 0})]


def test_ap_on_wpa2_when_password_set(monkeypatch):
    log = fake_network(monkeypatch)
    hardware.ap_on("Szczesliwy_Kurczak", "abcdefgh")
    assert log == [("active", 1, True), ("config", {"ssid": "Szczesliwy_Kurczak", "key": "abcdefgh", "security": 3})]


def test_ap_off_deactivates_ap_iface(monkeypatch):
    log = fake_network(monkeypatch)
    hardware.ap_off()
    assert log == [("active", 1, False)]
