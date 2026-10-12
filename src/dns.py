# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
# Captive-portal DNS: every A query gets the AP IP. Runs only while the AP is up (main.ap_session).
import socket

from compat import sleep_ms

DNS_PORT = 53
DNS_TTL_S = 60
HEADER_LEN = 12
MIN_QUERY_LEN = HEADER_LEN + 1 + 4  # header + root label + qtype/qclass


def build_reply(query, ip):
    """Reply to a single-question query with one A record = ip (4 bytes). Malformed -> None."""
    if len(query) < MIN_QUERY_LEN or len(ip) != 4:
        return None
    if query[2] & 0x80:  # QR=1: this is a response, not a query
        return None
    if query[4] != 0 or query[5] != 1:  # QDCOUNT must be 1
        return None
    i = HEADER_LEN
    while True:  # walk QNAME labels; compression pointers are not valid in a question
        if i >= len(query):
            return None
        n = query[i]
        if n == 0:
            break
        if n & 0xC0:
            return None
        i += 1 + n
    end = i + 5  # root label byte + qtype(2) + qclass(2)
    if end > len(query):
        return None
    flags = b"\x81\x80"
    counts = b"\x00\x01\x00\x01\x00\x00\x00\x00"  # QD=1 AN=1 NS=0 AR=0
    answer = b"\xc0\x0c" + b"\x00\x01\x00\x01" + DNS_TTL_S.to_bytes(4, "big") + b"\x00\x04" + ip
    # ponytail: answers every qtype with an A record; captive portal only needs A.
    return query[0:2] + flags + counts + query[HEADER_LEN:end] + answer


async def serve_dns(ip="192.168.4.1"):
    ip_bytes = bytes([int(p) for p in ip.split(".")])
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", DNS_PORT))
        sock.setblocking(False)
        while True:
            try:
                data, addr = sock.recvfrom(512)
            except OSError:  # EAGAIN: nothing queued
                await sleep_ms(50)
                continue
            reply = build_reply(data, ip_bytes)
            if reply is not None:
                try:
                    sock.sendto(reply, addr)
                except OSError:
                    pass  # best effort; client retries
            await sleep_ms(0)  # a query flood must not starve the loop (WDT)
    finally:
        sock.close()
