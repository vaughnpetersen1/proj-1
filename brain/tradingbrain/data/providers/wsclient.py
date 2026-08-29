"""Minimal RFC 6455 WebSocket client (stdlib only).

Written by hand rather than pulled from a package so the whole system keeps its
one-dependency promise (numpy) and runs on a machine that cannot reach PyPI.
It implements exactly what a market-data stream needs:

  * TLS handshake with the ``Sec-WebSocket-Accept`` check
  * masked client frames, continuation frames, and messages split across frames
  * ping/pong (auto-replies to server pings; can send its own heartbeat)
  * clean close handshake
  * a read timeout so a silent connection is detected instead of hanging forever

It deliberately does NOT implement: extensions, permessage-deflate, or the
server role. ``tests/test_websocket.py`` runs it against a real socket server to
prove the framing is right, since the live vendor stream cannot be reached from
the build environment.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import time
import urllib.parse
from typing import Any, Iterator

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT, OP_TEXT, OP_BINARY = 0x0, 0x1, 0x2
OP_CLOSE, OP_PING, OP_PONG = 0x8, 0x9, 0xA


class WebSocketError(RuntimeError):
    pass


class WebSocketClosed(WebSocketError):
    def __init__(self, code: int = 1000, reason: str = "") -> None:
        super().__init__(f"websocket closed ({code}): {reason}")
        self.code, self.reason = code, reason


class WebSocket:
    """A blocking client connection."""

    def __init__(self, url: str, headers: dict[str, str] | None = None,
                 timeout: float = 30.0, connect_timeout: float = 15.0) -> None:
        self.url = url
        self.timeout = timeout
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("ws", "wss"):
            raise WebSocketError(f"not a websocket url: {url!r}")
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        raw = socket.create_connection((host, port), timeout=connect_timeout)
        if parsed.scheme == "wss":
            ctx = ssl.create_default_context()
            raw = ctx.wrap_socket(raw, server_hostname=host)
        raw.settimeout(timeout)
        self.sock = raw
        self._buf = b""
        self._closed = False
        self._handshake(host, port, path, headers or {})

    # -- handshake ---------------------------------------------------------
    def _handshake(self, host: str, port: int, path: str,
                   headers: dict[str, str]) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {host}:{port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
        ]
        lines += [f"{k}: {v}" for k, v in headers.items()]
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())

        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WebSocketError("connection closed during handshake")
            head += chunk
        head, _, rest = head.partition(b"\r\n\r\n")
        self._buf = rest
        status_line, *hdr_lines = head.decode("latin-1").split("\r\n")
        if "101" not in status_line:
            raise WebSocketError(f"handshake rejected: {status_line}")
        got = {}
        for line in hdr_lines:
            k, _, v = line.partition(":")
            got[k.strip().lower()] = v.strip()
        expected = base64.b64encode(
            hashlib.sha1((key + GUID).encode()).digest()).decode()
        if got.get("sec-websocket-accept") != expected:
            raise WebSocketError("handshake failed: bad Sec-WebSocket-Accept")

    # -- io ----------------------------------------------------------------
    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise WebSocketClosed(1006, "connection closed by peer")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _read_frame(self) -> tuple[int, bool, bytes]:
        b1, b2 = self._read_exact(2)
        fin = bool(b1 & 0x80)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(length) if length else b""
        if masked:
            payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        return opcode, fin, payload

    def _send_frame(self, opcode: int, payload: bytes = b"") -> None:
        if self._closed:
            raise WebSocketClosed(1000, "already closed")
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", n)
        mask = os.urandom(4)
        header += mask
        masked = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    # -- public ------------------------------------------------------------
    def send_text(self, text: str) -> None:
        self._send_frame(OP_TEXT, text.encode())

    def send_json(self, obj: Any) -> None:
        self.send_text(json.dumps(obj))

    def ping(self, payload: bytes = b"hb") -> None:
        self._send_frame(OP_PING, payload)

    def recv(self) -> str | None:
        """One application message, or None when a control frame was handled."""
        opcode, fin, payload = self._read_frame()
        if opcode == OP_CLOSE:
            code, reason = 1005, ""
            if len(payload) >= 2:
                code = struct.unpack("!H", payload[:2])[0]
                reason = payload[2:].decode("utf-8", "replace")
            self._closed = True
            raise WebSocketClosed(code, reason)
        if opcode == OP_PING:
            self._send_frame(OP_PONG, payload)
            return None
        if opcode == OP_PONG:
            return None
        if opcode in (OP_TEXT, OP_BINARY):
            data = payload
            while not fin:
                op2, fin, more = self._read_frame()
                if op2 == OP_PING:
                    self._send_frame(OP_PONG, more)
                    continue
                if op2 != OP_CONT:
                    raise WebSocketError(f"expected continuation, got opcode {op2}")
                data += more
            return data.decode("utf-8", "replace")
        raise WebSocketError(f"unexpected opcode {opcode}")

    def messages(self) -> Iterator[str]:
        while True:
            msg = self.recv()
            if msg is not None:
                yield msg

    def close(self, code: int = 1000, reason: str = "") -> None:
        if not self._closed:
            try:
                self._send_frame(OP_CLOSE, struct.pack("!H", code) + reason.encode())
            except OSError:
                pass
            self._closed = True
        try:
            self.sock.close()
        except OSError:
            pass

    def __enter__(self) -> "WebSocket":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
