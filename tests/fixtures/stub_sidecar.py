"""Stub chat sidecar for hub tests (handoff M3.3 §11): a tiny http.server
implementing the §4 contract — no Node, no ~/.pi in CI.

Contract coverage: /health (reports busy + open SSE connection count),
/api/chat/{events,send,stop,new,history}. A send holds ``busy`` for
``BUSY_CLEAR_S`` so tests can observe the 409 mapping; the SSE stream emits
one ``text_delta`` immediately, then heartbeats — the open-connection count
serves the hub's disconnect-closes-upstream assertion. Announces
``SIDECAR_READY {json}``` on stdout once listening (the handshake format).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BUSY_CLEAR_S = 0.5  # how long one send keeps ``busy`` true (409 window)
_HEARTBEAT_S = 0.2

_state_lock = threading.Lock()
_busy = False
_open: list[BaseHTTPRequestHandler] = []


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # silence the test log
        pass

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        try:
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _body(self) -> bytes:
        length = int(self.headers.get("content-length") or 0)
        return self.rfile.read(length) if length else b""

    def do_GET(self) -> None:
        if self.path == "/health":
            with _state_lock:
                payload = {
                    "ok": True,
                    "busy": _busy,
                    "model": "stub/model",
                    "session_file": None,
                    "connections": len(_open),
                }
            self._json(200, payload)
        elif self.path == "/api/chat/history":
            self._json(
                200,
                {"messages": [{"role": "user", "text": "stub history line"}]},
            )
        elif self.path == "/api/chat/events":
            self._sse()
        else:
            self._json(404, {"error": f"not found: GET {self.path}"})

    def _sse(self) -> None:
        try:
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("cache-control", "no-cache")
            self.send_header("connection", "keep-alive")
            self.send_header("x-accel-buffering", "no")
            self.end_headers()
            self.wfile.write(b"retry: 2000\n\n")
            self.wfile.write(b'data: {"type": "text_delta", "delta": "hello from stub"}\n\n')
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
            return
        with _state_lock:
            _open.append(self)
        try:
            while True:
                time.sleep(_HEARTBEAT_S)
                self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _state_lock:
                if self in _open:
                    _open.remove(self)
            self.close_connection = True

    def do_POST(self) -> None:
        global _busy
        body = self._body()
        if self.path == "/api/chat/send":
            try:
                data = json.loads(body or b"null")
                text = data.get("text") if isinstance(data, dict) else None
            except ValueError:
                text = None
            if not isinstance(text, str) or not text.strip():
                self._json(400, {"error": "text must be a non-empty string"})
                return
            with _state_lock:
                if _busy:
                    self._json(409, {"error": "already processing a turn"})
                    return
                _busy = True
            timer = threading.Timer(BUSY_CLEAR_S, _clear_busy)
            timer.daemon = True
            timer.start()
            self._json(202, {"started": True})
        elif self.path == "/api/chat/stop":
            with _state_lock:
                _busy = False
            self._json(202, {"stopping": True})
        elif self.path == "/api/chat/new":
            self._json(202, {"reset": True})
        else:
            self._json(404, {"error": f"not found: POST {self.path}"})


def _clear_busy() -> None:
    global _busy
    with _state_lock:
        _busy = False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    port = int(server.server_address[1])
    print(
        f"SIDECAR_READY {json.dumps({'port': port, 'model': 'stub/model', 'session_file': None})}",
        flush=True,
    )
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


if __name__ == "__main__":
    main()
