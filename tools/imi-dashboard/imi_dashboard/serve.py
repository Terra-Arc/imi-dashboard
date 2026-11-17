"""``imi-dashboard serve``: a local web server for previewing a dashboard.

A thin layer over the standard library's ``http.server``. It adds what a
generated dashboard needs and plain ``python -m http.server`` does not
guarantee:

- Fixed ``Content-Type`` headers for the file types a dashboard contains.
  ``http.server`` otherwise takes them from the ``mimetypes`` module, which on
  Windows also reads the registry; a wrong registry entry for ``.js`` makes
  the browser refuse the web app's module script, and the page stays blank.
- ``Cache-Control: no-store`` on every response, so a dashboard re-converted
  while it is being previewed is never shown with data files left in the
  browser cache from before.
- A check that the folder is a generated dashboard at all.
- Requests are logged through the ``imi_dashboard`` logger, so they follow
  the command's -v, -q, --log-style and --log-file: a failed request (status
  400 and above) as a warning, so that a missing data file stands out instead
  of scrolling past among hundreds of period loads; any other as info (shown
  with -v); the reason http.server gives for a failure (``code 404, message
  File not found``), which the request line repeats, at debug (-vv).

It is for local viewing only: the ``http.server`` documentation warns
against using it in production. To share a dashboard, upload the folder to a
static host.
"""

from __future__ import annotations

import functools
import ipaddress
import logging
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger(__name__)

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".bin": "application/octet-stream",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


class DashboardRequestHandler(SimpleHTTPRequestHandler):
    # guess_type() consults extensions_map before falling back to mimetypes.
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, **CONTENT_TYPES}

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_request(self, code="-", size="-") -> None:
        """``127.0.0.1 "GET /data/manifest.json HTTP/1.1" 200``: a warning
        for status 400 and above, info otherwise."""
        status = getattr(code, "value", code)
        level = logging.WARNING if isinstance(status, int) and status >= 400 else logging.INFO
        log.log(level, '%s "%s" %s', self.address_string(), self.requestline, status)

    def log_error(self, format: str, *args) -> None:  # noqa: A002 (http.server's name)
        # send_error() calls this just before log_request() for the same
        # response, whose line already gives the status.
        log.debug(format, *args)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        log.info(format, *args)


def check_dashboard(folder: Path) -> None:
    """Raise ValueError if `folder` is not a complete generated dashboard."""
    if not folder.is_dir():
        raise ValueError(f"{folder} is not a folder")
    if not (folder / "data" / "manifest.json").is_file():
        raise ValueError(
            f"{folder} is not a generated dashboard (no data/manifest.json); "
            "point at the output folder of `imi-dashboard convert`"
        )
    if not (folder / "index.html").is_file():
        raise ValueError(f"{folder} has no index.html (its app bundle is missing); convert it again")


def make_server(folder: Path, bind: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    """Check `folder` and return a server for it, bound but not yet serving.
    Port 0 picks a free port."""
    check_dashboard(folder)
    handler = functools.partial(DashboardRequestHandler, directory=str(folder))
    return ThreadingHTTPServer((bind, port), handler)


def is_loopback(server: ThreadingHTTPServer) -> bool:
    """Whether `server` accepts connections from this machine only: it is
    bound to a loopback address (127.0.0.0/8 or ::1). The bound address is
    numeric even when --bind named a host, and 0.0.0.0 or :: (every
    interface) is not loopback."""
    host = server.server_address[0]
    if not isinstance(host, str):  # typed str | bytes | bytearray
        host = bytes(host).decode()
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False


def server_url(server: ThreadingHTTPServer) -> str:
    host, port = server.server_address[:2]
    if isinstance(host, bytes):  # typed str | bytes; an IPv4/IPv6 server gives str
        host = host.decode()
    if host in ("0.0.0.0", ""):
        host = "localhost"
    return f"http://{host}:{port}/"
