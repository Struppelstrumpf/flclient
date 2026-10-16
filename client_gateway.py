"""Outbound WSS to the FlyConomy client gateway. stdlib + ssl + certifi."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import socket
import ssl
import struct
import threading
import time
from typing import Any, Callable, Optional
from urllib.parse import urlparse

try:
    import certifi
except ImportError:
    certifi = None  # type: ignore

OP_TEXT = 0x1
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA
MAX_FRAME = 65536


DEFAULT_API_URL = "https://flyconomy.de/api"


def api_to_ws_url(api_url: str) -> str:
    parsed = urlparse(api_url.rstrip("/"))
    scheme = "wss" if parsed.scheme == "https" else "ws"
    host = parsed.netloc or parsed.path
    path = parsed.path if parsed.scheme else ""
    if path.endswith("/api"):
        ws_path = path + "/client/ws"
    elif path.endswith("/api/"):
        ws_path = path + "client/ws"
    else:
        ws_path = "/api/client/ws"
    return f"{scheme}://{host}{ws_path}"


def _mask(data: bytes) -> bytes:
    key = os.urandom(4)
    return key + bytes(b ^ key[i % 4] for i, b in enumerate(data))


class ClientGateway:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._sock: Optional[ssl.SSLSocket | socket.socket] = None
        self._send_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._connected = False
        self.on_command: Optional[Callable[[dict], None]] = None
        self.token_provider: Optional[Callable[[], str]] = None
        self.device_id_provider: Optional[Callable[[], str]] = None
        self.version_provider: Optional[Callable[[], str]] = None
        self.presence_provider: Optional[Callable[[], dict]] = None
        self.log: Callable[[str], None] = lambda _m: None
        self._last_presence = ""
        self._last_presence_at = 0.0

    @property
    def connected(self) -> bool:
        return self._connected and self._sock is not None

    def start(self) -> None:
        self._stop.clear()
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="fc-gateway", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._close()

    def send_json(self, obj: dict) -> bool:
        if not self.connected:
            return False
        try:
            self._send_frame(OP_TEXT, json.dumps(obj, ensure_ascii=False).encode("utf-8"))
            return True
        except Exception:
            self._connected = False
            return False

    def send_result(self, cmd_id: str, *, ok: bool, data: Any = None, error: str | None = None) -> None:
        payload: dict[str, Any] = {"type": "result", "id": cmd_id, "ok": ok}
        if data is not None:
            payload["data"] = data
            if isinstance(data, dict) and data.get("message"):
                payload["message"] = data.get("message")
        if error:
            payload["error"] = error
        self.send_json(payload)

    def maybe_presence(self, force: bool = False) -> None:
        if not self.connected or not self.presence_provider:
            return
        try:
            body = self.presence_provider() or {}
        except Exception:
            return
        raw = json.dumps(body, sort_keys=True, default=str)
        now = time.time()
        if not force and raw == self._last_presence and now - self._last_presence_at < 15:
            return
        self._last_presence = raw
        self._last_presence_at = now
        self.send_json({"type": "presence", **body})

    def _run(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            token = (self.token_provider() if self.token_provider else "") or ""
            if not token:
                time.sleep(2.0)
                continue
            try:
                self._session()
                delay = 1.0
            except Exception as e:
                jitter = random.random() * 3.0
                wait = min(30.0, delay) + jitter
                self.log(f"Gateway: reconnect in {wait:.0f}s ({e})")
                self._stop.wait(wait)
                delay = min(30.0, delay * 2)
            finally:
                self._connected = False
                self._close()

    def _session(self) -> None:
        self._connected = False
        url = api_to_ws_url(DEFAULT_API_URL)
        parsed = urlparse(url)
        host = parsed.hostname or "flyconomy.de"
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        path = parsed.path or "/api/client/ws"
        raw = socket.create_connection((host, port), timeout=20)
        sock: ssl.SSLSocket | socket.socket = raw
        if parsed.scheme == "wss":
            ctx = ssl.create_default_context(cafile=certifi.where() if certifi else None)
            sock = ctx.wrap_socket(raw, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        sock.sendall(req.encode("ascii"))
        buf = b""
        sock.settimeout(20)
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                raise RuntimeError("gateway handshake closed")
            buf += chunk
            if len(buf) > 8192:
                raise RuntimeError("gateway handshake too large")
        head, rest = buf.split(b"\r\n\r\n", 1)
        status = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status:
            raise RuntimeError(f"gateway handshake {status.decode('latin1', 'replace')[:80]}")
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        )
        accept = b""
        for line in head.split(b"\r\n")[1:]:
            if line.lower().startswith(b"sec-websocket-accept:"):
                accept = line.split(b":", 1)[1].strip()
        if accept != expected:
            raise RuntimeError("gateway accept mismatch")
        self._sock = sock
        self._leftover = rest
        self._frag = None
        token = self.token_provider() if self.token_provider else ""
        device = self.device_id_provider() if self.device_id_provider else ""
        version = self.version_provider() if self.version_provider else ""
        self._send_frame(
            OP_TEXT,
            json.dumps(
                {"type": "hello", "token": token, "device_id": device, "client_version": version}
            ).encode("utf-8"),
        )
        hello_deadline = time.time() + 15
        hello_ok = False
        sock.settimeout(5)
        last_ping = time.time()
        while not self._stop.is_set():
            if hello_ok and time.time() - last_ping >= 20:
                self.send_json({"type": "ping"})
                last_ping = time.time()
                self.maybe_presence()
            try:
                frame = self._read_frame()
            except socket.timeout:
                if not hello_ok and time.time() > hello_deadline:
                    raise RuntimeError("gateway hello timeout")
                continue
            if frame is None:
                raise RuntimeError("gateway closed")
            opcode, payload = frame
            if opcode == OP_CLOSE:
                break
            if opcode == OP_PING:
                self._send_frame(OP_PONG, payload)
                continue
            if opcode == OP_PONG:
                continue
            if opcode != OP_TEXT:
                continue
            try:
                msg = json.loads(payload.decode("utf-8"))
            except Exception:
                continue
            kind = str(msg.get("type") or "")
            if kind == "hello_ok":
                hello_ok = True
                self._connected = True
                self.log("Gateway verbunden")
                self.maybe_presence(force=True)
                continue
            if kind == "pong":
                continue
            if kind == "command" and self.on_command:
                try:
                    self.on_command(msg)
                except Exception as e:
                    self.send_result(str(msg.get("id") or ""), ok=False, error=str(e))
            if kind == "error":
                self.log(f"Gateway: {msg.get('error')}")
        self._close()

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        sock = self._sock
        if not sock:
            raise RuntimeError("not connected")
        header = bytearray([0x80 | opcode])
        ln = len(payload)
        if ln < 126:
            header.append(0x80 | ln)
        elif ln < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", ln))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", ln))
        with self._send_lock:
            sock.sendall(bytes(header) + _mask(payload))

    def _fill(self, n: int) -> None:
        """Make sure n bytes are buffered. A socket timeout leaves everything that
        was read in the buffer – the old _read_exact dropped it and the next frame
        was read from the middle of the previous one (desync → reconnect)."""
        sock = self._sock
        if not sock:
            raise RuntimeError("not connected")
        buf = getattr(self, "_leftover", b"")
        while len(buf) < n:
            try:
                chunk = sock.recv(max(n - len(buf), 4096))
            except socket.timeout:
                self._leftover = buf
                raise
            if not chunk:
                self._leftover = buf
                raise RuntimeError("gateway closed")
            buf += chunk
        self._leftover = buf

    def _read_exact(self, n: int) -> bytes:
        self._fill(n)
        buf = self._leftover
        out, self._leftover = buf[:n], buf[n:]
        return out

    def _parse_header(self) -> tuple[int, int, int, bool, int]:
        """(header length, payload length, opcode, masked, fin) of the buffered frame."""
        self._fill(2)
        b0, b1 = self._leftover[0], self._leftover[1]
        fin = (b0 >> 7) & 1
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        ln = b1 & 0x7F
        hlen = 2
        if ln == 126:
            self._fill(4)
            ln = struct.unpack("!H", self._leftover[2:4])[0]
            hlen = 4
        elif ln == 127:
            self._fill(10)
            ln = struct.unpack("!Q", self._leftover[2:10])[0]
            hlen = 10
        if ln > MAX_FRAME:
            raise RuntimeError("gateway frame too large")
        if masked:
            hlen += 4
        return hlen, ln, opcode, masked, fin

    def _read_one_frame(self) -> tuple[int, bytes, int]:
        """One complete frame or socket.timeout – nothing is consumed until the
        whole frame is in the buffer."""
        hlen, ln, opcode, masked, fin = self._parse_header()
        self._fill(hlen + ln)
        buf = self._leftover
        mask = buf[hlen - 4 : hlen] if masked else b""
        payload = buf[hlen : hlen + ln]
        self._leftover = buf[hlen + ln :]
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload, fin

    def _read_frame(self) -> Optional[tuple[int, bytes]]:
        """Next message; continuation frames (FIN=0) are joined."""
        while True:
            opcode, payload, fin = self._read_one_frame()
            if opcode >= 0x8:
                return opcode, payload  # control frames are never fragmented
            if opcode == 0x0:
                parts = getattr(self, "_frag", None)
                if parts is None:
                    continue  # stray continuation without a start – ignore it
                op0, data = parts
                data += payload
                if len(data) > MAX_FRAME * 4:
                    self._frag = None
                    raise RuntimeError("gateway message too large")
                if fin:
                    self._frag = None
                    return op0, data
                self._frag = (op0, data)
                continue
            if not fin:
                self._frag = (opcode, payload)
                continue
            self._frag = None
            return opcode, payload

    def _close(self) -> None:
        self._connected = False
        sock = self._sock
        self._sock = None
        if sock:
            try:
                sock.close()
            except Exception:
                pass


GATEWAY = ClientGateway()
