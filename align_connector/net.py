# -*- coding: utf-8 -*-
"""HTTP, through QGIS' own network stack.

Everything goes through ``QgsBlockingNetworkRequest`` rather than ``requests``
or ``urllib``, for three reasons that all bite in the field:

* **Proxies.** GIS teams often sit behind a corporate proxy. QGIS knows about
  it; a bare ``urllib`` call does not, and fails for the users least able to
  diagnose it.
* **No dependency.** A QGIS plugin cannot ``pip install``.
* **Certificates.** QGIS' SSL configuration, including any organisational CA,
  applies without us reimplementing it.

Raw header names are used instead of the ``KnownHeaders`` enum: the enum moved
namespace between Qt5 and Qt6, raw headers did not.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import json

from qgis.core import QgsBlockingNetworkRequest
from qgis.PyQt.QtCore import QByteArray, QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest

#: Marker for a transport failure — no HTTP status was ever received.
NO_RESPONSE = 0


class HttpResult:
    """A response reduced to what a caller actually decides on."""

    def __init__(self, status, body, error=None, headers=None):
        self.status = status
        self.body = body if body is not None else {}
        self.error = error
        #: Response headers, lower-cased. Needed by resumable uploads, where the
        #: store reports what it holds in `Range` rather than in a body.
        self.headers = headers or {}

    @property
    def ok(self):
        return 200 <= self.status < 300

    def message(self, fallback="Request failed"):
        """The most useful sentence we can show a human.

        ALIGN returns structured errors; OAuth endpoints use RFC 6749's
        ``error_description``. Falling back to a generic sentence beats showing
        a raw payload.
        """
        if isinstance(self.body, dict):
            for key in ("message", "error_description"):
                value = self.body.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        return self.error or fallback

    def code(self):
        """The machine-readable error code, when the server sent one."""
        if isinstance(self.body, dict):
            value = self.body.get("error")
            if isinstance(value, str):
                return value
        return None


def _qt_enum(name, member):
    """Read a Qt enum member across Qt5 and Qt6.

    Qt6 scoped the enums (`QNetworkRequest.Attribute.X`); Qt5 exposed them flat
    (`QNetworkRequest.X`). Looking them up by name keeps one source running on
    both, which is the whole point of importing through `qgis.PyQt`.
    """
    group = getattr(QNetworkRequest, name, QNetworkRequest)
    return getattr(group, member)


def _build(url, bearer=None, content_type=None, extra_headers=None, manual_redirects=False):
    request = QNetworkRequest(QUrl(url))
    if content_type:
        request.setRawHeader(b"Content-Type", content_type.encode("ascii"))
    request.setRawHeader(b"Accept", b"application/json")
    if bearer:
        request.setRawHeader(b"Authorization", "Bearer {}".format(bearer).encode("ascii"))
    for name, value in (extra_headers or {}).items():
        request.setRawHeader(name.encode("ascii"), str(value).encode("utf-8"))
    if manual_redirects:
        # A resumable upload answers 308 to mean "keep going". Qt treats 308 as
        # a redirect and would follow it, turning the store's progress report
        # into a lost request.
        request.setAttribute(
            _qt_enum("Attribute", "RedirectPolicyAttribute"),
            _qt_enum("RedirectPolicy", "ManualRedirectPolicy"),
        )
    return request


def _finish(blocking, err):
    reply = blocking.reply()
    status = NO_RESPONSE
    payload = None
    headers = {}
    if reply is not None:
        status = reply.attribute(_qt_enum("Attribute", "HttpStatusCodeAttribute")) or NO_RESPONSE
        for name, value in reply.rawHeaderPairs():
            headers[bytes(name).decode("latin-1").lower()] = bytes(value).decode("latin-1")
        raw = bytes(reply.content())
        if raw:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                payload = None
    if err != QgsBlockingNetworkRequest.NoError and status == NO_RESPONSE:
        return HttpResult(NO_RESPONSE, None, blocking.errorMessage() or "Network error", headers)
    return HttpResult(status, payload, None, headers)


def post_json(url, payload, bearer=None):
    """POST a JSON document and read a JSON answer."""
    request = _build(url, bearer=bearer, content_type="application/json")
    body = QByteArray(json.dumps(payload).encode("utf-8"))
    blocking = QgsBlockingNetworkRequest()
    return _finish(blocking, blocking.post(request, body))


def get_json(url, bearer=None):
    """GET a JSON document."""
    request = _build(url, bearer=bearer)
    blocking = QgsBlockingNetworkRequest()
    return _finish(blocking, blocking.get(request))


def put_bytes(url, data, headers=None, manual_redirects=False):
    """PUT raw bytes to an **opaque** URL, with the headers the API handed back.

    The URL and its headers come from ALIGN and are never constructed here:
    that is what keeps the plugin working whichever object store sits behind
    the platform (ADR-0045). Presuming a provider — a header prefix, a query
    parameter — would tie every installed copy to today's infrastructure.
    """
    request = _build(url, extra_headers=headers or {}, manual_redirects=manual_redirects)
    blocking = QgsBlockingNetworkRequest()
    err = blocking.put(request, QByteArray(data))
    # Object stores answer a bare 200 with no body; that is a success, not an
    # unparseable response.
    return _finish(blocking, err)
