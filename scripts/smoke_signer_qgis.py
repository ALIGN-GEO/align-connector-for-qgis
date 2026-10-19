#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove, inside QGIS, where the plugin's token goes (TCK-2348, CA-10).

    python scripts/smoke_signer_qgis.py     (with QGIS' Python: needs QGIS 3.22+)

Two local HTTP servers stand for ALIGN's API and for another origin. The
request signer is installed in QGIS' network manager, and real requests leave
through it:

* a request to a feature service's OGC API on the API's origin carries the
  session's bearer; another route of the API, the other origin, and a request
  that already carries credentials do not get it;
* a signed request redirected to the other origin is not followed there: the
  other server never sees the token;
* an OAPIF layer opened on the service, with no ``authcfg``, is valid, reads
  its features, and every one of its requests was signed;
* once the signer is removed, nothing is signed;
* without a token (signed out), the layer's requests go unsigned and ALIGN's
  401 is what QGIS gets (the server answers ``private, no-store``, as ALIGN's
  facade does: QGIS would otherwise serve the signed answers from its cache);
* below QGIS 3.36 the signer is never installed (the binding crashes there).

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import QgsApplication, QgsBlockingNetworkRequest, QgsVectorLayer  # noqa: E402
from qgis.PyQt.QtCore import QUrl  # noqa: E402
from qgis.PyQt.QtNetwork import QNetworkRequest  # noqa: E402

from align_connector.auth.request_signer import RequestSigner, TokenSnapshot, supported  # noqa: E402
from ogc_stub import LAYER, SERVICE, TOKEN, Recorder as _Recorder, serve as _serve  # noqa: E402


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def _get(url, authorization=None):
    request = QNetworkRequest(QUrl(url))
    if authorization:
        request.setRawHeader(b"Authorization", authorization)
    QgsBlockingNetworkRequest().get(request)


def _auth_of(port, path_end):
    return [auth for (p, path, auth) in _Recorder.seen if p == port and path.split("?")[0].endswith(path_end)]


def main():
    app = QgsApplication([], False)
    app.initQgis()
    from qgis.core import Qgis

    if not supported(Qgis.QGIS_VERSION_INT):
        # Up to 3.34 the binding crashes QGIS on install (see request_signer): never installed there.
        check(Qgis.QGIS_VERSION_INT < 33600, "only a QGIS before 3.36 refuses the signer")
        print("request signer: not installed on QGIS {} (needs 3.36 or later), as intended".format(Qgis.QGIS_VERSION))
        app.exitQgis()
        return
    api, other = _serve(), _serve()
    api_port, other_port = api.server_address[1], other.server_address[1]
    _Recorder.seen, _Recorder.other_port = [], other_port
    api_url = "http://127.0.0.1:{}".format(api_port)

    snapshot = TokenSnapshot()
    snapshot.set(TOKEN, time.time() + 3600)
    signer = RequestSigner(snapshot, lambda: api_url)
    signer.install()

    _get(api_url + SERVICE + "/collections")
    check(_auth_of(api_port, "/ogc/collections") == ["Bearer " + TOKEN], "a service request on the API's origin is signed")
    _get(api_url + "/api/v1/orgs/org-1/datastore")
    check(_auth_of(api_port, "/datastore") == [None], "another route of the API is not")
    _get("http://127.0.0.1:{}{}/conformance".format(other_port, SERVICE))
    check(_auth_of(other_port, "/conformance") == [None], "another origin is not")
    _get(api_url + SERVICE + "/conformance", authorization=b"Basic b3duOmNyZWRz")
    check(_auth_of(api_port, "/conformance") == ["Basic b3duOmNyZWRz"], "a request with its own credentials keeps them")

    _Recorder.seen = []
    _get(api_url + SERVICE + "/redirect-out")
    check(_auth_of(api_port, "/redirect-out") == ["Bearer " + TOKEN], "the redirecting request was signed")
    check(not [s for s in _Recorder.seen if s[0] == other_port], "and the redirect to another origin was not followed: the other server saw nothing")

    _Recorder.seen, _Recorder.require_token = [], True
    source = "url='{}{}' typename='{}' pagingEnabled='true' restrictToRequestBBOX='1'".format(api_url, SERVICE, LAYER)
    check("authcfg" not in source, "the layer's source carries no authcfg")
    layer = QgsVectorLayer(source, "Jardins (ALIGN)", "OAPIF")
    check(layer.isValid(), "the OAPIF layer opens, signed by the plugin alone")
    check(len(list(layer.getFeatures())) == 3, "and reads its features")
    service_requests = [s for s in _Recorder.seen if s[0] == api_port and s[1].startswith(SERVICE)]
    check(service_requests and all(auth == "Bearer " + TOKEN for (_p, _path, auth) in service_requests), "every request of the layer was signed")

    _Recorder.seen = []
    snapshot.clear()
    # Another data source string: QGIS shares what it read per source within one process.
    unsigned = QgsVectorLayer(source + " maxNumFeatures='100'", "Jardins (ALIGN)", "OAPIF")
    check(not unsigned.isValid(), "signed out, ALIGN's 401 is what the layer gets")
    check(all(auth is None for (_p, _path, auth) in _Recorder.seen), "and nothing was signed")

    snapshot.set(TOKEN, time.time() + 3600)
    signer.uninstall()
    _Recorder.seen = []
    _get(api_url + SERVICE + "/collections")
    check(_auth_of(api_port, "/ogc/collections") == [None], "once removed, the signer signs nothing")

    api.shutdown()
    other.shutdown()
    # The layers go before QGIS does: a provider destroyed after exitQgis() crashes the interpreter's teardown.
    del layer, unsigned
    print("request signer inside QGIS {}: all checks passed".format(__import__("qgis.core", fromlist=["Qgis"]).Qgis.QGIS_VERSION))
    app.exitQgis()


if __name__ == "__main__":
    main()
