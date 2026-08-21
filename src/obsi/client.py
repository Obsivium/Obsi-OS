"""Tiny newline-JSON client used by the unprivileged OBSI shell."""

from __future__ import annotations

import json
import socket
from pathlib import Path


class CoreError(RuntimeError):
    pass


def request(action: str, payload: dict | None = None, socket_path: Path = Path("/run/obsi/core.sock")) -> dict:
    message = json.dumps({"action": action, "payload": payload or {}}, separators=(",", ":")) + "\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(30)
        client.connect(str(socket_path))
        client.sendall(message.encode())
        received = bytearray()
        while b"\n" not in received:
            block = client.recv(65536)
            if not block:
                break
            received.extend(block)
            if len(received) > 2 * 1024 * 1024:
                raise CoreError("core response is too large")
    try:
        response = json.loads(bytes(received).split(b"\n", 1)[0])
    except (json.JSONDecodeError, IndexError) as exc:
        raise CoreError("invalid response from OBSI Core") from exc
    if not response.get("ok"):
        raise CoreError(str(response.get("error", "unknown OBSI Core error")))
    return response.get("data", {})
