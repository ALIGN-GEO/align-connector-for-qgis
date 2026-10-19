# -*- coding: utf-8 -*-
"""Signing the requests of ALIGN's feature-service layers (TCK-2348, CA-10).

A service layer is read and edited by QGIS' own OGC API Features provider
(``OAPIF``), not by the plugin. Until 0.6.0, QGIS' OAuth2 method signed it,
with a second consent and QGIS' own end page. Benjamin's decision of
2026-10-19 makes the plugin sign those requests itself, with the session it
already holds: one consent, no ``authcfg`` in the project, the pattern of a
native application (RFC 8252) and of ArcGIS Pro.

QGIS lets a plugin see each network request before it leaves
(``QgsNetworkAccessManager.setRequestPreprocessor``, Python since 3.22, the
plugin's floor). The preprocessor adds ``Authorization: Bearer`` to a request
**only** when all of this holds (the four conditions, condition 1):

* its origin is exactly the configured API's: same scheme, same host, same
  port. ``https://api.aligngeo.com.evil.test`` and ``http://api.aligngeo.com``
  are other origins, and never signed;
* its path is the OGC API of a feature service
  (``/api/v1/orgs/<org>/feature-services/<service>/ogc…``), with no ``.`` or
  ``..`` segment and no encoded dot or slash that would walk out of it;
* it carries no ``Authorization`` of its own (a layer the user signs with
  their own credentials keeps them);
* the session holds a token that has not expired.

A signed request may not follow a redirect to another origin
(``SameOriginRedirectPolicy``): the token never leaves for another host.

**QGIS 3.36 or later.** Up to the 3.34 series, QGIS' Python binding of
``setRequestPreprocessor`` builds its return value without holding Python's
lock (``sipConvertFromNewType`` before ``Py_END_ALLOW_THREADS``, fixed in
3.36): with Python 3.12, installing a preprocessor crashes QGIS. Below 3.36
the signer is never installed, and a service layer is not offered.

The token is read from a snapshot the session keeps on the interface thread:
QGIS calls the preprocessor on whatever thread fetches, and a network thread
must not unlock the credential store or refresh a token. The snapshot is
refreshed before it expires (:class:`TokenSnapshot`, fed by the plugin).

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import re
import threading
import time
from urllib.parse import urlsplit

#: The path, under the API's own path, of a feature service's OGC API.
_SERVICE_OGC_PATH = re.compile(r"^/api/v1/orgs/[A-Za-z0-9_-]+/feature-services/[A-Za-z0-9_-]+/ogc(?:/[^?#]*)?$")
_DEFAULT_PORTS = {"http": 80, "https": 443}
#: A request never leaves with a token this close to its expiry.
_EXPIRY_MARGIN_SEC = 30
#: The first QGIS whose preprocessor binding is safe to install (see above).
MIN_QGIS = 33600


def supported(qgis_version_int):
    """Whether this QGIS can carry the signer."""
    try:
        return int(qgis_version_int) >= MIN_QGIS
    except (TypeError, ValueError):
        return False


def _port(parts):
    try:
        return parts.port or _DEFAULT_PORTS.get(parts.scheme.lower())
    except ValueError:
        return None


def signable(url, api_url):
    """Whether ``url`` may carry the session's token (condition 1).

    :param url: the request's address, as QGIS is about to send it
    :param api_url: the API's base address the plugin is configured with
    """
    try:
        target = urlsplit(url or "")
        api = urlsplit(api_url or "")
    except ValueError:
        return False
    if not target.scheme or not api.scheme or target.scheme.lower() != api.scheme.lower():
        return False
    if not target.hostname or (target.hostname or "").lower() != (api.hostname or "").lower():
        return False
    if target.username is not None or target.password is not None:
        return False
    if _port(target) is None or _port(target) != _port(api):
        return False
    path = target.path
    lowered = path.lower()
    if "\\" in path or "%2e" in lowered or "%2f" in lowered or "%5c" in lowered:
        return False
    if any(segment in (".", "..") for segment in path.split("/")):
        return False
    base = api.path.rstrip("/")
    if base and not path.startswith(base + "/"):
        return False
    return bool(_SERVICE_OGC_PATH.match(path[len(base):]))


class TokenSnapshot(object):
    """The session's access token, readable from any thread.

    Written on the interface thread only (sign-in, refresh, sign-out); read by
    the preprocessor on the network threads. A token past its expiry, minus a
    margin, reads as no token: the request goes unsigned and ALIGN answers 401,
    which the layer shows, rather than a token that dies mid-request.
    """

    def __init__(self, clock=time.time):
        self._lock = threading.Lock()
        self._token = None
        self._expires_at = 0.0
        self._clock = clock

    def set(self, token, expires_at):
        with self._lock:
            self._token = token or None
            self._expires_at = float(expires_at or 0)

    def clear(self):
        self.set(None, 0)

    def current(self):
        with self._lock:
            if self._token and self._clock() < self._expires_at - _EXPIRY_MARGIN_SEC:
                return self._token
            return None

    def expires_at(self):
        with self._lock:
            return self._expires_at if self._token else 0.0


def sign(request, url, snapshot, api_url, header_of, set_header, restrict_redirects):
    """Sign one request when it may be signed; never raise.

    The QGIS binding hands the preprocessor's result to C++ as a string, and
    an exception would reach C++ too: this answers ``""`` whatever happens.

    :returns: ``True`` when the request was signed
    """
    try:
        if not signable(url, api_url):
            return False
        if header_of(request, b"Authorization"):
            return False
        token = snapshot.current()
        if not token:
            return False
        set_header(request, b"Authorization", "Bearer {}".format(token).encode("ascii"))
        restrict_redirects(request)
        return True
    except Exception:  # noqa: BLE001 — nothing may escape into C++; unsigned is the safe outcome
        return False


class RequestSigner(object):
    """The preprocessor installed in QGIS while the plugin is loaded."""

    def __init__(self, snapshot, api_url):
        self._snapshot = snapshot
        self._api_url = api_url
        self._id = None

    def install(self):
        from qgis.core import QgsNetworkAccessManager

        if self._id is None:
            self._id = QgsNetworkAccessManager.setRequestPreprocessor(self._preprocess)

    def uninstall(self):
        from qgis.core import QgsNetworkAccessManager

        if self._id is not None:
            QgsNetworkAccessManager.removeRequestPreprocessor(self._id)
            self._id = None

    def _preprocess(self, request):
        from qgis.PyQt.QtNetwork import QNetworkRequest

        sign(
            request,
            request.url().toString(),
            self._snapshot,
            self._api_url(),
            lambda r, name: r.hasRawHeader(name),
            lambda r, name, value: r.setRawHeader(name, value),
            lambda r: r.setAttribute(_redirect_policy_attribute(QNetworkRequest), _same_origin_policy(QNetworkRequest)),
        )
        return ""


def _redirect_policy_attribute(request_class):
    group = getattr(request_class, "Attribute", None)
    return getattr(group, "RedirectPolicyAttribute", None) or request_class.RedirectPolicyAttribute


def _same_origin_policy(request_class):
    group = getattr(request_class, "RedirectPolicy", None)
    return getattr(group, "SameOriginRedirectPolicy", None) or request_class.SameOriginRedirectPolicy
