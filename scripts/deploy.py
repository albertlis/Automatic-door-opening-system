"""Build src/ to .mpy (mpy-cross 1.29.0.post2 = MicroPython 1.29 bytecode) and deploy to the board.

Usage: uv run python scripts/deploy.py [COM11] [--first]
  --first  first flash only: also upload config.default.json -> :config.json (overwrites live config).
           Without it, :config.json is never touched.
       uv run python scripts/deploy.py --wifi PASSWORD [--host 192.168.4.1]
  --wifi   upload the same artifacts over WebREPL (ws://HOST:8266) instead of USB. AP must be up.
           No rm/mkdir over WebREPL, no config.json/logs.bin, --first refused.
"""

import argparse
import gzip
import pathlib
import subprocess
import tempfile
import urllib.request

import webrepl_client  # scripts/ is sys.path[0] when run as a script

ROOT = pathlib.Path(__file__).resolve().parents[1]
MPY_CROSS = ["uvx", "--from", "mpy-cross==1.29.0.post2", "mpy-cross"]
MODULES = ["astro", "compat", "config", "dns", "hardware", "logs", "state", "web"]
MICRODOT = ["__init__", "microdot"]
MICRODOT_SRC = ROOT / ".venv" / "Lib" / "site-packages" / "microdot"
DEVICE_MAIN = "import coop\ncoop.start()\n"  # ponytail: entry point lives on device only, not in repo
WIFI_HOST = "192.168.4.1"  # AP address, WebREPL on port 8266
# Never touched here: :config.json (except --first) and :logs.bin (event log, owned by device).


def mpremote(port: str, *args: str, check: bool = True) -> None:
    subprocess.run(["uvx", "mpremote", "connect", port, *args], check=check)


def _touch_ap(host: str) -> None:
    """GET /api/status: resets the device AP idle timer (src/main.py ap_session). Raises OSError on failure."""
    with urllib.request.urlopen(f"http://{host}/api/status", timeout=5) as resp:
        resp.read()


def wifi_deploy(host: str, password: str, build: pathlib.Path) -> None:
    """Same artifacts as USB mode, over WebREPL. Never config.json or logs.bin.

    coop.mpy goes last among .mpy: a partial upload leaves the old coop driving the old modules.
    Before each file an HTTP GET holds the AP idle timer (WebREPL traffic does not reset it).
    """
    coop = build / "coop.mpy"
    files = [(f.relative_to(build).as_posix(), f.read_bytes()) for f in sorted(build.rglob("*.mpy")) if f != coop]
    files.append(("www/index.html.gz", (build / "index.html.gz").read_bytes()))
    files.append(("coop.mpy", coop.read_bytes()))
    files.append(("boot.py", (ROOT / "src" / "boot.py").read_bytes()))
    files.append(("main.py", (build / "main.py").read_bytes()))  # last: device main.py starts the program
    print(
        "WARN wifi: cannot rm over WebREPL, device .py copies (if any) stay. "
        "Assumes :microdot and :www exist (USB deploy creates them)."
    )
    try:
        repl = webrepl_client.WebRepl(host, password)
    except OSError as e:
        raise SystemExit(f"AP not reachable / device web server down: WebREPL connect {host} failed: {e}") from e
    with repl:
        print("Remote WebREPL version:", repl.version())
        for i, (name, data) in enumerate(files, 1):
            try:
                _touch_ap(host)
            except OSError as e:
                if i == 1:
                    where = "before any file was sent"
                else:
                    where = f"after {i - 1} of {len(files)} files (partial upload: redeploy over USB)"
                raise SystemExit(f"AP not reachable / device web server down: GET failed {where}: {e}") from e
            repl.put_file("/" + name, data)
    print(f"uploaded {len(files)} files over WebREPL to {host} (build: {build})")
    # REPL-text machine.reset() is ignored while the asyncio app runs (measured): reboot via the web API.
    req = urllib.request.Request(f"http://{host}/api/reboot", method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        print("reboot:", resp.read().decode())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("port", nargs="?", default=None, help="USB COM port (default COM11)")
    parser.add_argument("--first", action="store_true", help="first flash: also upload config.default.json")
    parser.add_argument(
        "--wifi", metavar="PASSWORD", default=None, help="WiFi mode: upload over WebREPL instead of USB (AP must be up)"
    )
    parser.add_argument("--host", default=None, help=f"WiFi mode host (default {WIFI_HOST})")
    args = parser.parse_args()
    if args.wifi is None:
        if args.host is not None:
            parser.error("--host requires --wifi")
    elif args.first:
        parser.error("--first is USB only: WiFi mode never uploads config.json")
    elif args.port is not None:
        parser.error("COM port is USB only: do not combine it with --wifi")
    port: str = args.port or "COM11"

    build = pathlib.Path(tempfile.mkdtemp(prefix="coop_mpy_"))
    (build / "microdot").mkdir()
    jobs = [(ROOT / "src" / f"{m}.py", build / f"{m}.mpy") for m in MODULES]
    jobs.append((ROOT / "src" / "main.py", build / "coop.mpy"))
    jobs += [(MICRODOT_SRC / f"{m}.py", build / "microdot" / f"{m}.mpy") for m in MICRODOT]
    for src, dst in jobs:
        subprocess.run([*MPY_CROSS, "-o", str(dst), str(src)], check=True)
    (build / "main.py").write_bytes(DEVICE_MAIN.encode("ascii"))  # bytes: no CRLF on Windows
    gz = build / "index.html.gz"
    gz.write_bytes(gzip.compress((ROOT / "src" / "www" / "index.html").read_bytes(), compresslevel=9, mtime=0))

    if args.wifi is not None:
        wifi_deploy(args.host or WIFI_HOST, args.wifi, build)
        return

    for m in MODULES:
        mpremote(port, "fs", "rm", f":{m}.py", check=False)
    for m in MICRODOT:
        mpremote(port, "fs", "rm", f":microdot/{m}.py", check=False)
    mpremote(port, "fs", "mkdir", ":microdot", check=False)  # error if exists: ignored
    mpremote(port, "fs", "mkdir", ":www", check=False)  # error if exists: ignored
    for f in sorted(build.rglob("*.mpy")):
        mpremote(port, "fs", "cp", str(f), f":{f.relative_to(build).as_posix()}")
    mpremote(port, "fs", "cp", str(gz), ":/www/index.html.gz")
    mpremote(port, "fs", "cp", str(ROOT / "src" / "boot.py"), ":boot.py")  # plain .py: MP runs boot.py only as .py
    if args.first:
        mpremote(port, "fs", "cp", str(ROOT / "config.default.json"), ":config.json")
    mpremote(port, "fs", "cp", str(build / "main.py"), ":main.py")  # last: device main.py starts the program
    extra = " + config.json" if args.first else ""
    print(f"uploaded {len(jobs)} mpy, index.html.gz, boot.py, main.py{extra} to {port} (build: {build})")


if __name__ == "__main__":
    main()
