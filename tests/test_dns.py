# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
# Pure tests for src/dns.py build_reply (captive-portal DNS). No socket is opened here.
import pytest

from dns import build_reply

IP = bytes([192, 168, 4, 1])
ID = b"\x12\x34"
HEADER = ID + b"\x01\x00" + b"\x00\x01" + b"\x00\x00" * 3  # RD=1, QDCOUNT=1, AN/NS/AR=0
QUESTION = b"\x06kurnik\x03lan\x00" + b"\x00\x01" + b"\x00\x01"  # QNAME, QTYPE=A, QCLASS=IN
QUERY = HEADER + QUESTION  # 12 + 12 + 4 = 28 bytes
ANSWER_TAIL = b"\xc0\x0c" + b"\x00\x01\x00\x01" + b"\x00\x00\x00\x3c" + b"\x00\x04" + IP  # TTL 60


def _with(packet, offset, value):
    b = bytearray(packet)
    b[offset] = value
    return bytes(b)


def test_reply_to_kurnik_a_query():
    reply = build_reply(QUERY, IP)
    assert reply is not None
    assert reply[0:2] == ID  # same ID
    assert reply[2:4] == b"\x81\x80"  # QR=1, RD, RA
    assert reply[4:6] == b"\x00\x01"  # QDCOUNT 1
    assert int.from_bytes(reply[6:8], "big") == 1  # ANCOUNT 1
    assert reply[12:28] == QUESTION  # question echoed
    assert reply[28:] == ANSWER_TAIL
    assert reply.endswith(bytes.fromhex("C0A80401"))


@pytest.mark.parametrize(
    "packet",
    [
        pytest.param(b"", id="empty"),
        pytest.param(QUERY[:16], id="header-only-below-min"),
        pytest.param(QUERY[:27], id="question-truncated"),
        pytest.param(_with(QUERY, 2, 0x81), id="response-QR1"),
        pytest.param(_with(QUERY, 5, 2), id="qdcount-2"),
        pytest.param(HEADER + b"\xc0\x0c\x00\x01\x00\x01", id="compression-pointer-in-qname"),
    ],
)
def test_malformed_returns_none(packet):
    assert build_reply(packet, IP) is None
