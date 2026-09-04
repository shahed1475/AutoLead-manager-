"""
server.py — serves tests/research_agent/fixtures/ over plain localhost HTTP
for the benchmark runner. Never imported by the pytest suite or by
production code — a manual/CI-optional tool only.
"""
from __future__ import annotations

import functools
import http.server
import socket
import threading
from pathlib import Path
from typing import Callable, Tuple


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve_fixtures(directory: Path) -> Tuple[int, Callable[[], None]]:
    """Starts a background HTTP server rooted at `directory`. Returns
    (port, stop_fn) — call stop_fn() when done."""
    port = _free_port()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    def stop() -> None:
        httpd.shutdown()
        httpd.server_close()

    return port, stop
