# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
import sys

if sys.implementation.name == "micropython":
    from uasyncio import sleep_ms
    from utime import ticks_diff, ticks_ms
else:
    import time as _time

    def ticks_ms() -> int:
        return int(_time.time() * 1000)

    def ticks_diff(a: int, b: int) -> int:
        return a - b

    async def sleep_ms(ms: int) -> None:
        import asyncio

        await asyncio.sleep(ms / 1000)
