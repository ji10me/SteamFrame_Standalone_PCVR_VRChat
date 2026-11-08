#!/usr/bin/env python3
"""Use the running Steam client's loopback debugging endpoint over SSH.

No listener, Steam restart, external Python package, or credential collection.
Diagnostic helper: use only expressions scoped to the authorized VRChat task.
"""
import argparse
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import time
import urllib.request
from urllib.parse import urlparse


class WebSocket:
    def __init__(self, url):
        u = urlparse(url)
        if u.scheme != "ws" or u.hostname != "127.0.0.1" or u.port != 8080:
            raise ValueError("Only Steam's existing loopback endpoint is supported")
        self.sock = socket.create_connection((u.hostname, u.port), timeout=15)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {u.path} HTTP/1.1\r\nHost: 127.0.0.1:8080\r\n"
                           f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                           "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        head = bytearray()
        while not head.endswith(b"\r\n\r\n"):
            head += self.exact(1)
            if len(head) > 16384:
                raise ValueError("Oversize WebSocket handshake")
        expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        if not head.startswith(b"HTTP/1.1 101 ") or expected.lower() not in head.lower():
            raise ValueError("WebSocket handshake rejected")

    def exact(self, size):
        data = bytearray()
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise EOFError("Steam debugger connection closed")
            data += chunk
        return bytes(data)

    def send(self, payload, opcode=1):
        payload = payload.encode() if isinstance(payload, str) else payload
        n = len(payload)
        head = bytes([0x80 | opcode])
        head += bytes([0x80 | n]) if n < 126 else bytes([0xfe]) + struct.pack("!H", n) if n < 65536 else bytes([0xff]) + struct.pack("!Q", n)
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(payload)))

    def receive(self):
        parts = bytearray()
        while True:
            a, b = self.exact(2)
            size = b & 0x7f
            if size == 126:
                size = struct.unpack("!H", self.exact(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self.exact(8))[0]
            if size > 16 * 1024 * 1024:
                raise ValueError("Oversize debugger frame")
            mask = self.exact(4) if b & 0x80 else None
            data = self.exact(size)
            if mask:
                data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
            opcode = a & 15
            if opcode == 8:
                raise EOFError("Steam debugger sent close")
            if opcode == 9:
                self.send(data, 10)
                continue
            if opcode == 10:
                continue
            if opcode not in (0, 1):
                raise ValueError("Unexpected debugger frame")
            parts += data
            if len(parts) > 16 * 1024 * 1024:
                raise ValueError("Oversize debugger response")
            if a & 0x80:
                return json.loads(parts)

    def close(self):
        self.sock.close()


def evaluate(expression):
    targets = json.load(urllib.request.urlopen("http://127.0.0.1:8080/json", timeout=10))
    target = next((t for t in targets if t.get("title") == "SharedJSContext"), None)
    if not target:
        raise RuntimeError("Steam SharedJSContext is unavailable; do not restart Steam automatically")
    ws = WebSocket(target["webSocketDebuggerUrl"])
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
            "expression": expression, "returnByValue": True, "awaitPromise": True, "timeout": 15000}}))
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            response = ws.receive()
            if response.get("id") != 1:
                continue
            if "error" in response:
                raise RuntimeError(str(response["error"]))
            result = response["result"]
            if "exceptionDetails" in result:
                raise RuntimeError(str(result["exceptionDetails"]))
            return result["result"].get("value", result["result"].get("description"))
        raise TimeoutError("Steam debugger response timed out")
    finally:
        ws.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expression", help="Explicit diagnostic expression; never include credentials")
    args = parser.parse_args()
    try:
        print(json.dumps(evaluate(args.expression), indent=2, ensure_ascii=False))
    except (OSError, ValueError, RuntimeError, EOFError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
