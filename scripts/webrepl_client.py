"""Minimal WebREPL client for scripts/deploy.py --wifi. Stdlib only.

Derived from micropython/webrepl webrepl_cli.py (MIT License, see
https://github.com/micropython/webrepl/blob/master/LICENSE):
  Copyright (c) 2016 Damien P. George
  Copyright (c) 2016 Paul Sokolovsky
  Copyright (c) 2022 Jim Mussared
Kept: HTTP upgrade, password login, GET_VER, PUT_FILE, same request struct and
same frame split. Dropped: REPL console, GET_FILE, SANDBOX, builtin uwebsocket.
Added: raw REPL text frame (used for machine.reset()).

Self-check (no device needed): uv run python scripts/webrepl_client.py --selftest
"""

import socket
import struct
import threading

WEBREPL_PORT = 8266
REQ_S = "<2sBBQLH64s"  # identical to webrepl_cli.py WEBREPL_REQ_S
OP_PUT_FILE = 1
OP_GET_VER = 3
FRAME_TXT = 0x81
FRAME_BIN = 0x82
CHUNK = 1024
RESET_TEXT = b"import machine\r\nmachine.reset()\r\n"


def ws_frame(payload: bytes, frame: int = FRAME_BIN) -> bytes:
    """Unmasked websocket frame header + payload (same wire format as webrepl_cli.py)."""
    n = len(payload)
    if n < 126:
        hdr = struct.pack(">BB", frame, n)
    elif n < 65536:
        hdr = struct.pack(">BBH", frame, 126, n)
    else:
        raise ValueError(f"frame payload too large: {n}")
    return hdr + payload


class WebRepl:
    def __init__(self, host: str, password: str, port: int = WEBREPL_PORT, timeout: float = 10.0) -> None:
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.buf = b""
        self._handshake(host, port)
        self._login(password)

    def __enter__(self) -> "WebRepl":
        return self

    def __exit__(self, *exc: object) -> None:
        self.sock.close()

    def _handshake(self, host: str, port: int) -> None:
        self.sock.sendall(
            (
                f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: Upgrade\r\n"
                "Upgrade: websocket\r\nSec-WebSocket-Key: foo\r\n\r\n"
            ).encode("ascii")
        )
        head = b""
        while not head.endswith(b"\r\n\r\n"):
            c = self.sock.recv(1)
            if not c:
                raise ConnectionError("WebREPL: connection closed during HTTP handshake")
            head += c

    def _recv_exact(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("WebREPL: connection closed (wrong password, or AP down?)")
            data += chunk
        return data

    def _next_payload(self, text_ok: bool) -> bytes:
        while True:
            op, length = struct.unpack(">BB", self._recv_exact(2))
            if length == 126:
                (length,) = struct.unpack(">H", self._recv_exact(2))
            payload = self._recv_exact(length)
            if op == FRAME_BIN or (text_ok and op == FRAME_TXT):
                return payload
            # other record types skipped, as webrepl_cli.py does

    def _read(self, n: int, text_ok: bool = False) -> bytes:
        while len(self.buf) < n:
            self.buf += self._next_payload(text_ok)
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _login(self, password: str) -> None:
        while self._read(1, text_ok=True) != b":":
            pass
        if self._read(1, text_ok=True) != b" ":
            raise ConnectionError("WebREPL: unexpected login prompt")
        self.sock.sendall(ws_frame(password.encode("utf-8") + b"\r"))

    def _expect_ok(self, what: str) -> None:
        sig, code = struct.unpack("<2sH", self._read(4))
        if sig != b"WB" or code != 0:
            raise RuntimeError(f"WebREPL {what} failed: sig={sig!r} code={code}")

    def version(self) -> tuple[int, int, int]:
        rec = struct.pack(REQ_S, b"WA", OP_GET_VER, 0, 0, 0, 0, b"")
        self.sock.sendall(ws_frame(rec))
        major, minor, patch = struct.unpack("<BBB", self._read(3))
        return (major, minor, patch)

    def put_file(self, remote: str, data: bytes) -> None:
        fname = remote.encode("utf-8")
        if len(fname) > 64:
            raise ValueError(f"remote name longer than 64 bytes: {remote}")
        rec = struct.pack(REQ_S, b"WA", OP_PUT_FILE, 0, 0, len(data), len(fname), fname)
        self.sock.sendall(ws_frame(rec[:10]) + ws_frame(rec[10:]))
        self._expect_ok(f"open {remote}")
        for i in range(0, len(data), CHUNK):
            self.sock.sendall(ws_frame(data[i : i + CHUNK]))
        self._expect_ok(f"write {remote}")

    def reset(self) -> None:
        """Send machine.reset() as REPL text. Device runs it only if its REPL input is serviced."""
        self.sock.sendall(ws_frame(RESET_TEXT, FRAME_TXT))


def _selftest() -> None:
    # 1. header encoding at each length class (incl. 126 extended-length path)
    cases = [
        (0, b"\x82\x00"),
        (125, b"\x82\x7d"),
        (126, b"\x82\x7e\x00\x7e"),
        (1024, b"\x82\x7e\x04\x00"),
        (65535, b"\x82\x7e\xff\xff"),
    ]
    for n, hdr in cases:
        frame = ws_frame(b"x" * n)
        assert frame[: len(hdr)] == hdr, (n, frame[:4])
        assert len(frame) == len(hdr) + n
    assert ws_frame(RESET_TEXT, FRAME_TXT)[:2] == b"\x81" + bytes([len(RESET_TEXT)])

    # 2. full round trip against a fake device (independent frame decoder)
    payload = bytes(range(256)) * 10  # 2560 B -> 3 data frames
    seen: dict[str, object] = {}
    lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lsock.bind(("127.0.0.1", 0))
    lsock.listen(1)
    port = lsock.getsockname()[1]
    t = threading.Thread(target=_fake_device, args=(lsock, seen, payload), daemon=True)
    t.start()
    with WebRepl("127.0.0.1", "pw", port=port) as repl:
        ver = repl.version()
        repl.put_file("/microdot/x.mpy", payload)
        repl.reset()
    t.join(10)
    lsock.close()

    assert "error" not in seen, seen.get("error")
    assert ver == (1, 2, 3), ver
    assert seen["http"] == b"GET / HTTP/1.1", seen["http"]
    assert seen["password"] == b"pw\r", seen["password"]
    assert seen["ver_op"] == OP_GET_VER, seen["ver_op"]
    assert seen["hdr_frames"] == (10, 72), seen["hdr_frames"]
    assert seen["put_op"] == OP_PUT_FILE and seen["put_fname"] == "/microdot/x.mpy"
    assert seen["put_size"] == len(payload), seen["put_size"]
    assert seen["data_frames"] == [1024, 1024, 512] and seen["data_ok"] is True
    assert seen["reset"] == (FRAME_TXT, RESET_TEXT), seen["reset"]
    print("headers ok:", [len(f) for _, f in cases])
    print("login pw:", seen["password"], "| GET_VER op:", seen["ver_op"], "| version:", ver)
    print("put_file:", seen["put_fname"], "size", seen["put_size"], "frames", seen["data_frames"])
    print("reset frame:", seen["reset"])
    print("selftest OK")


class _ServerFrames:
    def __init__(self, conn: socket.socket) -> None:
        self.conn = conn

    def _exact(self, n: int) -> bytes:
        out = b""
        while len(out) < n:
            c = self.conn.recv(n - len(out))
            if not c:
                raise EOFError("client closed")
            out += c
        return out

    def recv(self) -> tuple[int, bytes]:
        op, ln = struct.unpack(">BB", self._exact(2))
        if ln == 126:
            (ln,) = struct.unpack(">H", self._exact(2))
        return op, self._exact(ln)


def _fake_device(lsock: socket.socket, seen: dict[str, object], payload: bytes) -> None:
    try:
        conn, _ = lsock.accept()
        with conn:
            head = b""
            while not head.endswith(b"\r\n\r\n"):
                head += conn.recv(1)
            seen["http"] = head.split(b"\r\n", 1)[0]
            conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n")
            frames = _ServerFrames(conn)
            conn.sendall(ws_frame(b"Password: ", FRAME_TXT))
            _, seen["password"] = frames.recv()
            _, req = frames.recv()
            seen["ver_op"] = struct.unpack(REQ_S, req)[1]
            conn.sendall(ws_frame(b"\x01\x02\x03"))
            _, hdr1 = frames.recv()
            _, hdr2 = frames.recv()
            seen["hdr_frames"] = (len(hdr1), len(hdr2))
            _sig, op, _r, _q, size, flen, fname = struct.unpack(REQ_S, hdr1 + hdr2)
            seen["put_op"] = op
            seen["put_size"] = size
            seen["put_fname"] = fname[:flen].decode("utf-8")
            conn.sendall(ws_frame(b"WB\x00\x00"))
            got = b""
            sizes: list[int] = []
            while len(got) < size:
                _, chunk = frames.recv()
                sizes.append(len(chunk))
                got += chunk
            seen["data_frames"] = sizes
            seen["data_ok"] = got == payload
            conn.sendall(ws_frame(b"WB\x00\x00"))
            seen["reset"] = frames.recv()
    except Exception as e:  # reported by selftest main thread
        seen["error"] = repr(e)


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["--selftest"]:
        _selftest()
    else:
        sys.exit("usage: webrepl_client.py --selftest  (device use: scripts/deploy.py --wifi)")
