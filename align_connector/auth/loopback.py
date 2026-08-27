# -*- coding: utf-8 -*-
"""The one-shot HTTP listener that catches the browser redirect (RFC 8252 §7.3).

The browser cannot hand a result back to a desktop application directly, so the
application briefly becomes a web server on the loopback interface and the
browser is redirected to it.

Three properties matter, and each closes a real hole:

* **Loopback only.** The socket binds ``127.0.0.1``, never ``0.0.0.0``: nothing
  outside the machine can reach it.
* **One shot.** It serves the redirect, answers the browser, and stops. A
  listener left running is a door left open.
* **State-checked.** Any local process can hit ``127.0.0.1``. Receiving *a*
  request is not receiving *ours*: a request whose ``state`` does not match is
  answered and ignored, and the wait continues.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

#: How long we wait for the human to approve before giving up (seconds).
DEFAULT_TIMEOUT_SEC = 300

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title>
<style>
 body {{ font: 15px/1.6 -apple-system, "Segoe UI", system-ui, sans-serif;
        background: #f5f2ec; color: #1c1b1a; display: grid; place-items: center;
        height: 100vh; margin: 0; }}
 main {{ max-width: 26rem; text-align: center; padding: 2rem; }}
 h1 {{ font-size: 1.25rem; font-weight: 500; margin: 0 0 .5rem; }}
 p {{ margin: 0; color: #57534e; }}
</style></head>
<body><main><h1>{title}</h1><p>{message}</p></main></body></html>
"""


class _Handler(BaseHTTPRequestHandler):
    """Serves exactly one redirect, then lets the server stop."""

    # Set by the server instance before serving.
    expected_state = None
    result = None

    def do_GET(self):  # noqa: N802 — name imposed by BaseHTTPRequestHandler
        params = parse_qs(urlparse(self.path).query)
        state = params.get("state", [None])[0]

        if self.expected_state is not None and state != self.expected_state:
            # Not our redirect. Answer politely, record nothing, keep waiting.
            self._respond(
                "Unexpected request",
                "This page was not opened by QGIS. You can close it.",
                status=400,
            )
            return

        error = params.get("error", [None])[0]
        code = params.get("code", [None])[0]

        if error:
            self.server.result = {"error": error}
            self._respond(
                "Authorization refused",
                "Nothing was granted. You can close this tab and return to QGIS.",
            )
        elif code:
            self.server.result = {"code": code}
            self._respond(
                "You are signed in",
                "ALIGN Connector is connected. You can close this tab and return to QGIS.",
            )
        else:
            self.server.result = {"error": "invalid_response"}
            self._respond(
                "Something went wrong",
                "The response carried neither an authorization code nor an error.",
                status=400,
            )

    def _respond(self, title, message, status=200):
        body = _PAGE.format(title=title, message=message).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # This page is a dead end by design: nothing to cache, nothing to link.
        self.send_header("Cache-Control", "no-store")
        self.wfile.write(b"")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        """Silence the default stderr logging — it would print the query string,
        and the query string carries the authorization code."""
        return


class LoopbackReceiver:
    """A loopback listener bound to an ephemeral port, used once.

    Usage::

        receiver = LoopbackReceiver(expected_state)
        redirect_uri = receiver.redirect_uri  # give this to the server
        ...open the browser...
        result = receiver.wait()  # {"code": ...} or {"error": ...} or None
    """

    def __init__(self, expected_state, timeout_sec=DEFAULT_TIMEOUT_SEC):
        # Port 0: the OS picks a free port. RFC 8252 requires the server to
        # accept any port on loopback precisely because the client cannot
        # reserve one in advance.
        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        self._server.result = None
        self._server.timeout = timeout_sec
        _Handler.expected_state = expected_state
        self._timeout_sec = timeout_sec
        self._thread = None

    @property
    def port(self):
        return self._server.server_address[1]

    @property
    def redirect_uri(self):
        """What we tell ALIGN to redirect to. An IP literal, never ``localhost``:
        a resolvable name can be pointed elsewhere."""
        return "http://127.0.0.1:{}/callback".format(self.port)

    def start(self):
        """Begin listening, without blocking the caller."""
        self._thread = threading.Thread(target=self._serve, name="align-oauth-loopback")
        self._thread.daemon = True
        self._thread.start()

    def _serve(self):
        # handle_request() honours `timeout`; loop so a stray local request
        # (rejected on state) does not consume our one shot.
        deadline_hits = 0
        while self._server.result is None and deadline_hits < 2:
            self._server.handle_request()
            if self._server.result is None:
                deadline_hits += 1

    def wait(self):
        """Block until the redirect arrives, or the timeout expires.

        :returns: ``{"code": ...}``, ``{"error": ...}``, or ``None`` on timeout.
        """
        if self._thread is not None:
            self._thread.join(self._timeout_sec + 1)
        return self._server.result

    def close(self):
        """Stop listening. Safe to call more than once."""
        try:
            self._server.server_close()
        except OSError:
            pass
