# -*- coding: utf-8 -*-
"""HTTP, through QGIS' own network stack.

Everything goes through QGIS' network classes rather than ``requests`` or
``urllib``, for three reasons that all bite in the field:

* **Proxies.** GIS teams often sit behind a corporate proxy. QGIS knows about
  it; a bare ``urllib`` call does not, and fails for the users least able to
  diagnose it.
* **No dependency.** A QGIS plugin cannot ``pip install``.
* **Certificates.** QGIS' SSL configuration, including any organisational CA,
  applies without us reimplementing it.

Two of those classes are used, for one reason. ``QgsBlockingNetworkRequest``
carries the JSON calls to ALIGN: it follows redirects, which an API may
legitimately answer with. It cannot carry a resumable upload: the store
answers ``308`` to mean "keep going", Qt files an empty redirect target on
that answer even without a ``Location`` header, and the blocking class follows
any target it finds, whatever policy the request asked for. The chunks go
through the thread's ``QgsNetworkAccessManager`` directly, which never follows
anything on its own (TCK-2195).

Raw header names are used instead of the ``KnownHeaders`` enum: the enum moved
namespace between Qt5 and Qt6, raw headers did not.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import json

from qgis.core import QgsBlockingNetworkRequest, QgsNetworkAccessManager
from qgis.PyQt.QtCore import QByteArray, QEventLoop, QUrl
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest

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


def _qt_enum(owner, name, member):
    """Read a Qt enum member across Qt5 and Qt6.

    Qt6 scoped the enums (`QNetworkRequest.Attribute.X`); Qt5 exposed them flat
    (`QNetworkRequest.X`). Looking them up by name keeps one source running on
    both, which is the whole point of importing through `qgis.PyQt`.
    """
    group = getattr(owner, name, owner)
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
            _qt_enum(QNetworkRequest, "Attribute", "RedirectPolicyAttribute"),
            _qt_enum(QNetworkRequest, "RedirectPolicy", "ManualRedirectPolicy"),
        )
    return request


def _headers_of(reply):
    """Response headers, lower-cased, from either reply class.

    `QgsBlockingNetworkRequest.reply()` hands back a QgsNetworkReplyContent,
    not a QNetworkReply: it exposes the header NAMES and a lookup, where the
    Qt class exposes pairs. Both offer these two accessors, so one reader
    serves both. Found the only way this kind of thing is found — by running
    it against a real server.
    """
    headers = {}
    for name in reply.rawHeaderList():
        key = bytes(name).decode("latin-1").lower()
        headers[key] = bytes(reply.rawHeader(name)).decode("latin-1")
    return headers


def _reduce(status, headers, raw, failed, error_message):
    """Reduce one answer to an :class:`HttpResult`, whichever class fetched it.

    A transport failure with no status at all is the only case reported as an
    error: a 4xx or 5xx carries a status, and the status is the verdict. An
    object store's bare 200 with no body is a success, not an unparseable
    response.
    """
    payload = None
    if raw:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            payload = None
    if failed and status == NO_RESPONSE:
        return HttpResult(NO_RESPONSE, None, error_message or "Network error", headers)
    return HttpResult(status, payload, None, headers)


def _finish(blocking, err):
    reply = blocking.reply()
    status = NO_RESPONSE
    headers = {}
    raw = b""
    if reply is not None:
        status = reply.attribute(_qt_enum(QNetworkRequest, "Attribute", "HttpStatusCodeAttribute")) or NO_RESPONSE
        headers = _headers_of(reply)
        raw = bytes(reply.content())
    failed = err != _qt_enum(QgsBlockingNetworkRequest, "ErrorCode", "NoError")
    return _reduce(status, headers, raw, failed, blocking.errorMessage())


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


def put_bytes(url, data, headers=None):
    """PUT raw bytes to an **opaque** URL, with the headers the API handed back.

    The URL and its headers come from ALIGN and are never constructed here:
    that is what keeps the plugin working whichever object store sits behind
    the platform (ADR-0045). Presuming a provider — a header prefix, a query
    parameter — would tie every installed copy to today's infrastructure.

    Sent through the thread's ``QgsNetworkAccessManager``, not through
    ``QgsBlockingNetworkRequest``, and never following a redirect. A resumable
    upload answers ``308`` to mean "keep going", with no ``Location`` header;
    Qt still files an empty redirect target on that answer (``checkForRedirect``
    in ``qnetworkreplyhttpimpl.cpp``), and the blocking class follows any
    target it finds, whatever redirect policy the request asked for. The
    store's progress report thus became a request to ``""``, read as a dropped
    connection, and every file larger than one chunk died after four retries
    (TCK-2195). The manager keeps what the blocking class gave us: QGIS' proxy,
    its certificates and its inactivity timeout.
    """
    request = _build(url, extra_headers=headers or {}, manual_redirects=True)
    reply = QgsNetworkAccessManager.instance().put(request, QByteArray(data))
    _wait(reply)
    try:
        status = reply.attribute(_qt_enum(QNetworkRequest, "Attribute", "HttpStatusCodeAttribute")) or NO_RESPONSE
        failed = reply.error() != _qt_enum(QNetworkReply, "NetworkError", "NoError")
        return _reduce(status, _headers_of(reply), bytes(reply.readAll()), failed, reply.errorString())
    finally:
        reply.deleteLater()


def _wait(reply):
    """Block the calling thread until the reply is finished.

    The manager is asynchronous; the upload runs in a task thread that has
    nothing else to do, so a local event loop is the right tool — the same one
    ``QgsBlockingNetworkRequest`` runs inside. The manager's own inactivity
    timeout aborts a silent connection and finishes the reply, so this cannot
    wait forever.
    """
    if reply.isFinished():
        return
    loop = QEventLoop()
    reply.finished.connect(loop.quit)
    if reply.isFinished():
        return
    # PyQt5 named it ``exec_`` (``exec`` was a keyword in the Python 2 days);
    # PyQt6 dropped the underscore.
    runner = getattr(loop, "exec", None) or getattr(loop, "exec_")
    runner()
