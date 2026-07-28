"""A local, dependency-free web UI.

``http.server`` from the standard library, bound to the loopback interface.
Two endpoints: the bundled examples, and an optimize call that returns the
whole report as JSON.  The page it serves is the same renderer that
``qizil report`` inlines into a standalone file.
"""

from __future__ import annotations

import contextlib
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .payload import build, list_examples

__all__ = ["serve", "make_server", "render_page", "STATIC"]

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 8 * 1024 * 1024


def render_page(data: dict | None = None) -> str:
    """The single-page app, with CSS and JS inlined (and data, for reports)."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    embedded = ""
    if data is not None:
        payload = json.dumps(data).replace("</", "<\\/")
        embedded = f"window.QIZIL_DATA = {payload};"
    return (
        html.replace("/*[CSS]*/", css)
        .replace("/*[DATA]*/", embedded)
        .replace("/*[JS]*/", js)
    )


class _Handler(BaseHTTPRequestHandler):
    server_version = "qizil"
    quiet = True

    # -- plumbing -------------------------------------------------------
    def log_message(self, fmt, *args):  # pragma: no cover - noise control
        if not self.quiet:
            super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with contextlib.suppress(BrokenPipeError):  # client may navigate away
            self.wfile.write(body)

    def _json(self, status: int, data: dict) -> None:
        self._send(status, json.dumps(data).encode("utf-8"), "application/json")

    # -- routes ---------------------------------------------------------
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send(200, render_page().encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/examples":
            self._json(200, {"examples": list_examples()})
        elif path == "/healthz":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.split("?")[0] != "/api/optimize":
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._json(413, {"error": "module too large"})
            return
        try:
            request = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            self._json(400, {"error": f"bad request: {exc}"})
            return

        source = request.get("source")
        if not source:
            self._json(400, {"error": "no module supplied"})
            return
        try:
            data = build(
                source,
                name=request.get("name") or "<pasted>",
                level=int(request.get("level", 3)),
                gateset=request.get("gateset", "auto"),
                preserve_global_phase=bool(request.get("preserve_global_phase")),
                verify=bool(request.get("verify", True)),
            )
        except Exception as exc:  # surfaced in the UI's status bar
            self._json(400, {"error": str(exc)})
            return
        from .. import __version__

        data["version"] = __version__
        self._json(200, data)


def make_server(host: str = "127.0.0.1", port: int = 8731, quiet: bool = True):
    """Build the server without running it (port 0 picks a free one)."""
    _Handler.quiet = quiet
    return ThreadingHTTPServer((host, port), _Handler)


def serve(
    host: str = "127.0.0.1",
    port: int = 8731,
    open_browser: bool = True,
    quiet: bool = True,
) -> None:
    """Run the UI until interrupted."""
    httpd = make_server(host, port, quiet)
    url = f"http://{host}:{httpd.server_address[1]}/"
    print(f"  qizil ui  ->  {url}")
    print("  ctrl-c to stop")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")
    finally:
        httpd.server_close()
