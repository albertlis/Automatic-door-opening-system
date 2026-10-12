# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
# Async tasks around the sync CoopController (docs/phases/04-async-tasks.md §1-§4, §10, §11).
import asyncio
import gc
import os

import hardware
import logs
from compat import sleep_ms, ticks_diff, ticks_ms
from config import load
from hardware import RTC, Motor, PcfLed
from state import CoopController, GateState

try:
    import machine
    from machine import Pin
except ImportError:  # CPython tests: no machine module
    machine = None
    Pin = None

LIGHTSLEEP_OK = False  # True only after 08 V3 + V4 pass on device (R25)
ap_request = False  # module globals (R26): no holder class
ap_active = False
ap_auto_done = False

IDLE_STATES = (GateState.IDLE_OPEN, GateState.IDLE_CLOSED, GateState.MANUAL_HOLD_OPEN, GateState.MANUAL_HOLD_CLOSED)
MOVING_STATES = (GateState.MOVING_OPEN, GateState.MOVING_CLOSE)
AP_IDLE_MS = 600_000  # 10 min since last HTTP request (R10, R35)


def _can_lightsleep(ctrl, btns):
    return (
        LIGHTSLEEP_OK
        and ctrl.state in IDLE_STATES
        and ctrl.rtc_synced is True
        and not ap_active
        and not ap_request
        and btns[0].value() == 1
        and btns[1].value() == 1
    )


async def control_loop(ctrl, wdt, btns, machine_mod):
    global ap_request, ap_auto_done
    while True:
        wdt.feed()
        ctrl.tick()
        if not ap_auto_done and ctrl.rtc_synced is False:  # None (never read) does not trigger (R31)
            ap_request = True
            ap_auto_done = True
        if _can_lightsleep(ctrl, btns):
            wdt.feed()
            machine_mod.lightsleep(2000)
            await asyncio.sleep(0)  # R25: other tasks must run
        else:
            await sleep_ms(2000)


async def _wait_stable(pin, level, ms):
    t = ticks_ms()
    while ticks_diff(ticks_ms(), t) < ms:
        if pin.value() != level:
            return False
        await sleep_ms(10)
    return True


async def button_task(ctrl, pin, other, action):
    global ap_request
    while True:
        if pin.value() != 0 or not await _wait_stable(pin, 0, 50):
            await sleep_ms(20)
            continue
        t0 = ticks_ms()  # kept across failed release checks (R34)
        both_t0 = None
        while True:
            now = ticks_ms()
            if other.value() != 0:  # other released: AP needs both low continuously (adversary #1)
                both_t0 = None
            elif both_t0 is None and await _wait_stable(other, 0, 50):
                both_t0 = now
            if both_t0 is not None and ticks_diff(now, both_t0) >= 3000:
                ap_request = True
            if pin.value() == 1:
                rel = ticks_ms()
                if await _wait_stable(pin, 1, 50):
                    break
            await sleep_ms(10)
        if both_t0 is None and ticks_diff(rel, t0) < 1000:
            ctrl.manual_move(action, "button")


def _set_led(led, v):
    try:
        led.value(v)
    except OSError:  # PCF write failed: skip this LED, keep the loop alive (test 14)
        pass


async def led_task(ctrl, red, green, yellow):
    # Blink phase from ticks_ms, not from a loop counter: slow = 1 Hz, fast = 4 Hz.
    while True:
        st = ctrl.state
        t = ticks_ms()
        slow = (t // 500) % 2
        fast = (t // 125) % 2
        r = g = y = 0
        if st == GateState.INIT:  # all off (R23)
            pass
        else:
            if st in (GateState.IDLE_OPEN, GateState.MANUAL_HOLD_OPEN):
                g = 1
            elif st in (GateState.IDLE_CLOSED, GateState.MANUAL_HOLD_CLOSED):
                r = 1
            elif st in MOVING_STATES:
                g = slow
            elif st == GateState.SAFETY_STOP:
                r = slow
            elif st == GateState.ERROR:
                r = fast
            if ctrl.rtc_synced is not True:  # overlay: RTC unsynced or OSF (R31)
                y = slow
        _set_led(red, r)
        _set_led(green, g)
        _set_led(yellow, y)
        await sleep_ms(125 if st == GateState.ERROR else 500)


def _touch_cb(http):
    def touch():  # before_request hook in web.py calls this (R10)
        http["last"] = ticks_ms()

    return touch


async def _serve(app, box):
    # Stores the crash; ap_session re-raises it after the poll loop (MicroPython Task has no cancelled/result).
    try:
        await app.start_server(port=80)
    except Exception as e:
        box["err"] = e


async def ap_session(ctrl, app_factory, wifi, machine_mod, cfg):
    global ap_request, ap_active
    while True:
        await sleep_ms(500)
        if not ap_request:
            continue
        if ap_active:  # no double start
            ap_request = False
            continue
        if ctrl.state in MOVING_STATES:  # decision 3: stays pending while moving
            continue
        ap_request = False
        ap_active = True
        http = {"last": ticks_ms()}  # set BEFORE awaiting the server (R35)
        app = None
        srv = None
        dns_task = None
        webrepl_mod = None
        box = {}
        try:
            wifi.ap_on("Szczesliwy_Kurczak", cfg["wifi"]["ap_password"])  # "" = open AP (R6)

            wr_pw = cfg["wifi"].get("webrepl_password", "")  # "" = WebREPL off; .get: tests pass bare wifi dicts
            if wr_pw:
                import webrepl  # lazy: only while the AP is up

                webrepl_mod = webrepl  # set before start: stop() still runs if start() half-binds
                try:
                    webrepl.start(password=wr_pw)
                except Exception as e:  # optional feature: must not take the config AP down
                    print("webrepl start:", e)

            from dns import serve_dns  # lazy: only needed while the AP is up

            dns_task = asyncio.create_task(serve_dns())  # captive portal: any name -> 192.168.4.1
            app = app_factory(ctrl, _touch_cb(http))
            srv = asyncio.create_task(_serve(app, box))
            while not srv.done():  # inline idle watchdog, same poll loop (R35)
                await sleep_ms(500)
                if ticks_diff(ticks_ms(), http["last"]) > AP_IDLE_MS:
                    break
            if "err" in box:  # server crash propagates -> supervised -> reset
                raise box["err"]
        finally:
            # Fixed order, each step best-effort. shutdown runs exactly once (this is the only call site).
            if app is not None:
                try:
                    app.shutdown()
                except Exception as e:
                    print("ap shutdown:", e)
            if srv is not None and not srv.done():
                srv.cancel()
            if dns_task is not None and not dns_task.done():
                dns_task.cancel()  # serve_dns closes its socket in finally
            if webrepl_mod is not None:
                try:
                    webrepl_mod.stop()
                except Exception as e:
                    print("webrepl stop:", e)
            try:
                wifi.ap_off()
            except Exception as e:
                print("ap_off:", e)
            ap_active = False


async def supervised(name, coro):  # R27 crash policy
    try:
        await coro
    except Exception as e:
        print(name, "crashed:", e)
        try:
            logs.flush()
        except Exception:
            pass
        machine.reset()


def boot_checks(ctrl, boot_ok):  # R18, R31, R12
    global ap_request, ap_auto_done
    if not boot_ok:
        ctrl.enter_error()  # public; never _halt from main
        ap_request = True  # auto AP on boot ERROR (visible without cable)
        ap_auto_done = True


async def run(ctrl, wdt, btns, leds, machine_mod, wifi, app_factory, cfg):
    t = asyncio.create_task
    t(supervised("control_loop", control_loop(ctrl, wdt, btns, machine_mod)))
    t(supervised("button_open", button_task(ctrl, btns[0], btns[1], "open")))
    t(supervised("button_close", button_task(ctrl, btns[1], btns[0], "close")))
    t(supervised("led", led_task(ctrl, leds[0], leds[1], leds[2])))
    t(supervised("ap", ap_session(ctrl, app_factory, wifi, machine_mod, cfg)))
    await asyncio.Event().wait()  # one await, run forever


def make_on_event_log(ctrl):
    def on_event_log(kind, trigger):
        ll = ctrl._last_local  # never _date (R5)
        if ll is not None:
            logs.record(logs.days_since_2025(ll[0], ll[1], ll[2]), ll[3], logs.KIND_BY_NAME[kind], trigger)
        if kind == "error":
            logs.flush()  # R29: flush on entering ERROR

    return on_event_log


def on_day_flush(prev, new):
    logs.flush()


def make_diag_cb(i2c, top, bottom, nfault, b_open, b_close):
    """GET /api/diag payload (UI contract: keys fixed). Never raises: bus/AP errors -> False/None."""
    rtc_drv = RTC(i2c)

    def diag():
        try:
            found = i2c.scan()
        except OSError:
            found = []
        rtc_ok = 0x68 in found
        osf = None
        if rtc_ok:
            try:
                osf = rtc_drv.osf()
            except OSError:
                osf = None
        clients = None
        try:
            import network

            ap = network.WLAN(network.AP_IF)
            if ap.active():
                clients = len(ap.status("stations"))
        except OSError:
            clients = None
        st = os.statvfs("/")  # (f_bsize, f_frsize, f_blocks, f_bfree, f_bavail, ...)
        return {
            "rtc": rtc_ok,
            "pcf": 0x20 in found,
            "osf": osf,
            "top": top.value(),
            "bottom": bottom.value(),
            "nfault": nfault.value(),
            "b_open": b_open.value(),
            "b_close": b_close.value(),
            "mhz": machine.freq() // 1000000,
            "mem": gc.mem_free() // 1024,  # free KB
            "fs_kb": st[1] * st[4] // 1024,  # free KB
            "up_s": ticks_ms() // 1000,
            "clients": clients,
        }

    return diag


def start():  # 08 V22: device entry. coop.mpy (name 'coop') calls it from the 2-line device main.py
    import web  # create_app(ctrl, touch_cb, ...) matches src/web.py:55

    i2c = hardware.get_i2c()  # shared singleton (08 §2)
    found = i2c.scan()
    if not ({0x20, 0x68} <= set(found)):
        hardware.recover_bus()  # 9 SCL clocks, resets singletons (08 §2)
        i2c = hardware.get_i2c()
        found = i2c.scan()
    boot_ok = {0x20, 0x68} <= set(found)
    pcf = hardware.get_pcf()  # singleton: boot.py 0xF3 cache shared
    try:
        pcf.write(0xF3)  # coast, LEDs off (08 V9)
    except OSError:
        boot_ok = False
    cfg = load("/config.json")
    top, bottom, nfault = Pin(0, Pin.IN, Pin.PULL_UP), Pin(1, Pin.IN, Pin.PULL_UP), Pin(20, Pin.IN)
    ctrl = CoopController(
        Motor(pcf, in1=2, in2=3),
        RTC(i2c),
        top,
        bottom,
        Pin(21, Pin.OUT),
        nfault,
        cfg,
    )  # nSLEEP LOW from boot.py (08 V13)
    ctrl.on_event = make_on_event_log(ctrl)  # bound before boot_checks so the ERROR flush runs (06 §5)
    ctrl.on_day_change = on_day_flush
    boot_checks(ctrl, boot_ok)
    wdt = machine.WDT(timeout=30_000)
    btns = (Pin(7, Pin.IN, Pin.PULL_UP), Pin(10, Pin.IN, Pin.PULL_UP))
    leds = (Pin(6, Pin.OUT), PcfLed(pcf, 0), PcfLed(pcf, 1))  # red, green, yellow
    wifi = hardware  # DI object: hardware.ap_on(ssid, password) / hardware.ap_off()
    diag_cb = make_diag_cb(i2c, top, bottom, nfault, btns[0], btns[1])
    app_factory = lambda *a, **k: web.create_app(*a, diag_cb=diag_cb, **k)  # noqa: E731
    asyncio.run(run(ctrl, wdt, btns, leds, machine, wifi, app_factory, cfg))


if __name__ == "__main__":  # 08 V22: MicroPython __name__ for main.py
    start()
