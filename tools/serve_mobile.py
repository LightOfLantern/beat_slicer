#!/usr/bin/env python3
"""Serve the standalone mobile build to an iPhone on the same Wi-Fi network."""

from __future__ import annotations

import argparse
import contextlib
import http.server
import os
import socket
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def local_ip() -> str:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as sock:
        try:
            sock.connect(("192.0.2.1", 80))
            return sock.getsockname()[0]
        except OSError:
            return "<MAC-IP>"


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", ""):
            self.send_response(302)
            self.send_header("Location", "/beat_slicer_mobile.html")
            self.end_headers()
            return
        super().do_GET()

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    build = ROOT / "beat_slicer_mobile.html"
    if not build.is_file():
        raise SystemExit("run `python3 tools/build_mobile.py` first")
    os.chdir(ROOT)
    server = http.server.ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print("Open this exact URL in Safari on the iPhone:")
    print(f"  http://{local_ip()}:{args.port}/beat_slicer_mobile.html")
    print("Mac and iPhone must be on the same Wi-Fi. Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
