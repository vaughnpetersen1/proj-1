"""The hand-rolled WebSocket client, proven against a real socket server.

The vendor stream is unreachable from the build environment, so correctness of
the framing is established here instead: a minimal RFC 6455 server (written
independently, in this file) exchanges text, fragmented, large, ping/pong and
close frames with the client.
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading
import time

import pytest

from tradingbrain.data.providers.wsclient import (WebSocket, WebSocketClosed,
                                                  WebSocketError)

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# --------------------------------------------------------------------------
# a deliberately independent server implementation
# --------------------------------------------------------------------------

def _server_handshake(conn: socket.socket) -> bool:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(4096)
        if not chunk:
            return False
        data += chunk
    key = ""
    for line in data.decode("latin-1").split("\r\n"):
        if line.lower().startswith("sec-websocket-key:"):
            key = line.split(":", 1)[1].strip()
    accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
    conn.sendall((
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
    return True


def _server_send(conn: socket.socket, opcode: int, payload: bytes, fin: bool = True) -> None:
    header = bytearray([(0x80 if fin else 0) | opcode])
    n = len(payload)
    if n < 126:
        header.append(n)
    elif n < 65536:
        header.append(126)
        header += struct.pack("!H", n)
    else:
        header.append(127)
        header += struct.pack("!Q", n)
    conn.sendall(bytes(header) + payload)          # server frames are unmasked


def _server_recv(conn: socket.socket) -> tuple[int, bytes]:
    def read(n):
        buf = b""
        while len(buf) < n:
            c = conn.recv(n - len(buf))
            if not c:
                raise ConnectionError("closed")
            buf += c
        return buf
    b1, b2 = read(2)
    opcode = b1 & 0x0F
    masked = bool(b2 & 0x80)
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack("!H", read(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", read(8))[0]
    mask = read(4) if masked else b""
    payload = read(length) if length else b""
    if masked:
        payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
    return opcode, payload


class EchoServer:
    """Serves one connection, running `script(conn)`."""

    def __init__(self, script):
        self.script = script
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.error: Exception | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            conn, _ = self.sock.accept()
            conn.settimeout(10)
            if _server_handshake(conn):
                self.script(conn)
            conn.close()
        except Exception as exc:                       # noqa: BLE001
            self.error = exc
        finally:
            self.sock.close()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/stream"


# --------------------------------------------------------------------------

def test_handshake_and_text_roundtrip():
    def script(conn):
        op, payload = _server_recv(conn)
        assert op == 0x1
        _server_send(conn, 0x1, b"echo:" + payload)
    srv = EchoServer(script)
    with WebSocket(srv.url, timeout=10) as ws:
        ws.send_text("hello")
        assert ws.recv() == "echo:hello"


def test_client_frames_are_masked():
    """RFC 6455 requires every client->server frame to be masked."""
    seen = {}

    def script(conn):
        raw = conn.recv(4096)
        seen["masked"] = bool(raw[1] & 0x80)
        _server_send(conn, 0x1, b"ok")
    srv = EchoServer(script)
    with WebSocket(srv.url, timeout=10) as ws:
        ws.send_text("x" * 10)
        ws.recv()
    assert seen["masked"] is True


def test_json_helper_and_large_payload():
    big = {"symbols": ["SYM%04d" % i for i in range(4000)]}

    def script(conn):
        op, payload = _server_recv(conn)
        _server_send(conn, 0x1, payload)
    srv = EchoServer(script)
    with WebSocket(srv.url, timeout=15) as ws:
        ws.send_json(big)
        assert json.loads(ws.recv()) == big


def test_fragmented_message_is_reassembled():
    def script(conn):
        _server_send(conn, 0x1, b'{"a":', fin=False)
        _server_send(conn, 0x0, b'1,"b":', fin=False)
        _server_send(conn, 0x0, b'2}', fin=True)
    srv = EchoServer(script)
    with WebSocket(srv.url, timeout=10) as ws:
        assert json.loads(ws.recv()) == {"a": 1, "b": 2}


def test_server_ping_is_answered_with_pong():
    got = {}

    def script(conn):
        _server_send(conn, 0x9, b"ping-payload")
        op, payload = _server_recv(conn)
        got["opcode"] = op
        got["payload"] = payload
        _server_send(conn, 0x1, b"done")
    srv = EchoServer(script)
    with WebSocket(srv.url, timeout=10) as ws:
        assert ws.recv() is None            # control frame, no application message
        assert ws.recv() == "done"
    assert got["opcode"] == 0xA             # PONG
    assert got["payload"] == b"ping-payload"


def test_close_frame_raises_with_code_and_reason():
    def script(conn):
        _server_send(conn, 0x8, struct.pack("!H", 1011) + b"going away")
    srv = EchoServer(script)
    ws = WebSocket(srv.url, timeout=10)
    with pytest.raises(WebSocketClosed) as e:
        ws.recv()
    assert e.value.code == 1011
    assert "going away" in e.value.reason
    ws.close()


def test_abrupt_disconnect_is_reported_as_closed():
    def script(conn):
        conn.close()
    srv = EchoServer(script)
    ws = WebSocket(srv.url, timeout=10)
    with pytest.raises(WebSocketClosed):
        ws.recv()
    ws.close()


def test_rejects_non_websocket_url():
    with pytest.raises(WebSocketError):
        WebSocket("https://example.com/")


def test_messages_iterator_skips_control_frames():
    def script(conn):
        _server_send(conn, 0x9, b"hb")
        _server_send(conn, 0x1, b"one")
        _server_send(conn, 0x9, b"hb")
        _server_send(conn, 0x1, b"two")
        time.sleep(0.05)
        _server_send(conn, 0x8, struct.pack("!H", 1000))
    srv = EchoServer(script)
    ws = WebSocket(srv.url, timeout=10)
    out = []
    try:
        for m in ws.messages():
            out.append(m)
    except WebSocketClosed:
        pass
    ws.close()
    assert out == ["one", "two"]
