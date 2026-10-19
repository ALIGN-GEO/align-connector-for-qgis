#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove where the plugin's token may go (TCK-2348, CA-10, condition 1).

    python scripts/smoke_request_signer.py

Runs on a plain interpreter. What is under test: a request is signed only
when its origin is exactly the configured API's and its path is the OGC API
of a feature service; a neighbouring origin (``api.aligngeo.com.evil.test``,
``http`` for ``https``, another port, credentials in the address), another
route of the API, or a path that walks out of the service is never signed; a
request that carries its own credentials keeps them; an expired token signs
nothing; nothing escapes the preprocessor. ``scripts/smoke_signer_qgis.py``
proves the same on real requests, inside QGIS.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import qgis_stubs  # noqa: E402

qgis_stubs.install()

from align_connector.auth.request_signer import TokenSnapshot, sign, signable  # noqa: E402

API = "https://api.aligngeo.com"
OGC = API + "/api/v1/orgs/org-1/feature-services/svc-jardins/ogc"


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def signed_urls():
    yes = [
        OGC,
        OGC + "/",
        OGC + "/collections",
        OGC + "/collections/0123456789abcdef/items?limit=1000&offset=1000",
        OGC + "/collections/0123456789abcdef/items/12",
        "https://API.AlignGeo.com:443/api/v1/orgs/org-1/feature-services/svc-jardins/ogc/conformance",
    ]
    for url in yes:
        check(signable(url, API), "signed: " + url)


def never_signed_urls():
    no = [
        # Another origin that looks like ALIGN's.
        "https://api.aligngeo.com.evil.test/api/v1/orgs/org-1/feature-services/svc-jardins/ogc",
        "http://api.aligngeo.com/api/v1/orgs/org-1/feature-services/svc-jardins/ogc",
        "https://api.aligngeo.com:8443/api/v1/orgs/org-1/feature-services/svc-jardins/ogc",
        "https://evil.test/api/v1/orgs/org-1/feature-services/svc-jardins/ogc",
        "https://aligngeo.com/api/v1/orgs/org-1/feature-services/svc-jardins/ogc",
        "https://user:pass@api.aligngeo.com/api/v1/orgs/org-1/feature-services/svc-jardins/ogc",
        "https://api.aligngeo.com@evil.test/api/v1/orgs/org-1/feature-services/svc-jardins/ogc",
        # Another route of the API: the token is the plugin's, not the layer's to spend.
        API + "/api/v1/orgs/org-1/datastore",
        API + "/api/v1/orgs/org-1/feature-services/svc-jardins",
        API + "/api/v1/orgs/org-1/feature-services/svc-jardins/ogcx",
        API + "/api/v1/oauth/token",
        # A path that walks out of the service.
        OGC + "/../../../datastore",
        OGC + "/%2e%2e/%2e%2e/datastore",
        OGC + "/collections%2F..%2Fdatastore",
        OGC + "/collections\\..\\x",
        # Not a web address at all.
        "file:///etc/passwd",
        "",
        "not a url",
    ]
    for url in no:
        check(not signable(url, API), "never signed: " + repr(url))


def api_under_a_path():
    api = "https://example.org/align"
    check(signable(api + "/api/v1/orgs/o/feature-services/s/ogc/collections", api), "an API under a path signs its own services")
    check(not signable("https://example.org/api/v1/orgs/o/feature-services/s/ogc", api), "but not the same path outside it")
    local = "http://127.0.0.1:3001"
    check(signable(local + "/api/v1/orgs/o/feature-services/s/ogc", local), "a local http API signs its own services")
    check(not signable("http://127.0.0.1:3002/api/v1/orgs/o/feature-services/s/ogc", local), "never another local port")


class _Request(object):
    def __init__(self, authorization=None):
        self.headers = {}
        if authorization:
            self.headers[b"Authorization"] = authorization
        self.restricted = False


def _sign(request, url, snapshot):
    return sign(
        request,
        url,
        snapshot,
        API,
        lambda r, name: name in r.headers,
        lambda r, name, value: r.headers.__setitem__(name, value),
        lambda r: setattr(r, "restricted", True),
    )


def signing():
    now = [1000.0]
    snapshot = TokenSnapshot(clock=lambda: now[0])
    request = _Request()
    check(not _sign(request, OGC, snapshot), "no session, nothing signed")
    snapshot.set("alg_at_live", 1000.0 + 3600)
    request = _Request()
    check(_sign(request, OGC + "/collections", snapshot), "a live token signs a service request")
    check(request.headers[b"Authorization"] == b"Bearer alg_at_live", "as a bearer")
    check(request.restricted, "and the request may not follow a redirect to another origin")
    other = _Request()
    check(not _sign(other, API + "/api/v1/orgs/org-1/datastore", snapshot), "another route is never signed")
    check(b"Authorization" not in other.headers, "and carries nothing")
    own = _Request(authorization=b"Basic dXNlcjpwYXNz")
    check(not _sign(own, OGC, snapshot), "a request with its own credentials keeps them")
    check(own.headers[b"Authorization"] == b"Basic dXNlcjpwYXNz", "untouched")
    now[0] = 1000.0 + 3600 - 10
    check(not _sign(_Request(), OGC, snapshot), "a token about to expire signs nothing")
    snapshot.set("alg_at_live", 1000.0 + 7200)
    snapshot.clear()
    check(not _sign(_Request(), OGC, snapshot), "a signed-out session signs nothing")

    def broken(_request, _name):
        raise RuntimeError("QGIS gave the preprocessor something odd")

    snapshot.set("alg_at_live", now[0] + 3600)
    check(not sign(_Request(), OGC, snapshot, API, broken, None, None), "nothing escapes the preprocessor")


def main():
    signed_urls()
    never_signed_urls()
    api_under_a_path()
    signing()
    print("request signer: all checks passed")


if __name__ == "__main__":
    main()
