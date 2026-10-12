# boot.py — runs before main.py on every reset. No loops.
# E402: imports below are after GPIO/settle code on purpose (nSLEEP LOW must be the first action).
from machine import Pin

# 1. nSLEEP LOW first line. Driver asleep before anything else runs.
Pin(21, Pin.OUT, value=0)

# 2. Settle (100-500 ms; v1 used 1 s).
import time  # noqa: E402

time.sleep_ms(300)  # VERIFY window (V12)

# 3. Radio off at boot. AP is started by ap_session (04 §11), never by boot (decision 3, R8).
import network  # noqa: E402

# Init the WiFi driver once while the IDF heap is still empty: it keeps its big DRAM block.
# Without this, imports grow the MicroPython heap into IDF DRAM and a later active(True)
# panics (Load access fault 0x2c, NULL malloc in the driver; measured on MP 1.29 C3).
# Keep before the step 4 freq call: active(True) hung the board at a lower clock (seen once, unconfirmed).
network.WLAN(network.AP_IF).active(True)
network.WLAN(network.AP_IF).active(False)
network.WLAN(network.STA_IF).active(False)  # VERIFY STA off needed (V10)

# 4. CPU fixed 80 MHz (WiFi minimum). 160 did not fix the AP OOM; real fix = .mpy deploy (R41).
# Never changed at runtime.
import machine  # noqa: E402

machine.freq(80_000_000)

# 5. I2C scan. One bus recovery attempt if 0x20 or 0x68 missing. No flag: main.py scans again (R40).
import hardware  # noqa: E402

found = hardware.get_i2c().scan()
if not ({0x20, 0x68} <= set(found)):
    hardware.recover_bus()

# 6. PCF8574 first write: motor coast, LEDs off (chip power-on = 0xFF = brake).
try:
    hardware.get_pcf().write(0xF3)
except OSError:
    print("boot: PCF8574 write failed")  # main.py re-checks the bus (R40)

# 7. Event log file.
import logs  # noqa: E402

logs.init_log()

# Boot done. MicroPython now runs main.py.
