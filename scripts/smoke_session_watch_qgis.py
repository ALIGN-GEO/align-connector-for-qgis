#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove, inside QGIS, what happens when ALIGN ends the session (TCK-2348, patch 0003).

    python scripts/smoke_session_watch_qgis.py

Needs QGIS 3.36 or later (the signer's floor); skipped below. Against a local
OGC API Features server that stands for ALIGN, with the plugin's real session
and signer, what is under test:

* a revocation while a layer reads: ALIGN's 401 makes the session ask ALIGN,
  once for the burst; refused, it ends with ALIGN's reason, the local tokens
  go, and nothing is signed any more (CA-16, CA-20, CA-21);
* QGIS's raw error for that ALIGN layer is taken back from the message bar;
  any other message stays (CA-19);
* an edit saved after a revocation: refused, the session ends, and the
  changes stay in QGIS's edit buffer (CA-26);
* a project opened after a revocation made while QGIS was closed: the session
  is asked before the layers load, and ends (CA-23); a project without ALIGN
  service layers asks nothing.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import os
import pathlib
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import Qgis, QgsApplication, QgsFeature, QgsProject, QgsVectorLayer  # noqa: E402
from qgis.PyQt.QtCore import QObject, pyqtSignal  # noqa: E402

from ogc_stub import LAYER, SERVICE, TOKEN, Recorder, serve  # noqa: E402

ENDED = (400, {"error": "invalid_grant", "error_description": "Unknown refresh token", "align_session": "ended"})


def check(condition, message):
    if not condition:
        raise AssertionError(message)


class _Store(object):
    """The credential store, in memory: QGIS's vault needs a master password."""

    def __init__(self):
        self.access, self.expires, self.refresh = TOKEN, time.time() + 3000, "alg_rt_1"

    def has_session(self):
        return bool(self.refresh)

    def access_token(self):
        return self.access

    def access_expires_at(self):
        return self.expires

    def refresh_token(self):
        return self.refresh

    def update_access_token(self, access_token, access_expires_at, refresh_token=None, scopes=None):
        self.access, self.expires = access_token, access_expires_at
        self.refresh = refresh_token or self.refresh

    def clear(self):
        self.access, self.expires, self.refresh = None, 0.0, None


class _Item(object):
    def __init__(self, title, text):
        self._title, self._text = title, text

    def title(self):
        return self._title

    def text(self):
        return self._text


class _Bar(QObject):
    widgetAdded = pyqtSignal(object)  # noqa: N815 — QGIS's name

    def __init__(self):
        super().__init__()
        self.popped = []

    def popWidget(self, item):  # noqa: N802 — QGIS's name
        self.popped.append(item)


class _Iface(object):
    def __init__(self):
        self.bar = _Bar()

    def messageBar(self):  # noqa: N802 — QGIS's name
        return self.bar


def _wait(app, condition, seconds=5):
    deadline = time.time() + seconds
    while time.time() < deadline and not condition():
        app.processEvents()
        time.sleep(0.02)
    return condition()


def _token_calls():
    return [entry for entry in Recorder.seen if entry[1] == "POST /api/v1/oauth/token"]


def main():
    app = QgsApplication([], False)
    app.initQgis()
    if Qgis.QGIS_VERSION_INT < 33600:
        print("session watch: needs QGIS 3.36 or later, skipped on {}".format(Qgis.QGIS_VERSION))
        app.exitQgis()
        return

    server = serve()
    api_url = "http://127.0.0.1:{}".format(server.server_address[1])
    from align_connector import config

    previous = (config.app_url(), config.api_url())
    config.set_endpoints(previous[0], api_url)
    from align_connector.auth import session as session_module
    from align_connector.auth.request_signer import RequestSigner
    from align_connector.publish import service_layer, service_memory
    from align_connector.session_watch import SessionWatch

    store = _Store()
    session_module.token_store = store
    session = session_module.AlignSession()
    ended = []
    session.signed_out.connect(lambda: ended.append(session.end_reason))
    session.prime_signer()
    signer = RequestSigner(session.snapshot, config.api_url)
    signer.install()
    iface = _Iface()
    watch = SessionWatch(iface, session, service_layer.service_layers)
    watch.attach()
    Recorder.seen, Recorder.require_token, Recorder.token, Recorder.refresh_answer = [], True, TOKEN, ENDED

    # -- a revocation while a layer reads (CA-16, CA-20, CA-21) -------------
    ref = service_memory.ServiceRef("org-1", "svc-jardins", LAYER, "jardins", None, service_memory.SERVICE)
    layer = service_layer.open_service(ref)
    check(layer.isValid() and len(list(layer.getFeatures())) == 3, "signed, the layer reads")
    Recorder.token = "a token ALIGN issued since"  # Settings → Integrations: the one QGIS holds is refused
    for _ in range(3):
        layer.dataProvider().reloadData()
        list(layer.getFeatures())
    check(_wait(app, lambda: ended), "ALIGN's 401 ended the session: {}".format(Recorder.seen[-4:]))
    check(ended == [session_module.ENDED_BY_ALIGN], "with ALIGN's reason: withdrawn, not expired (CA-21)")
    check(not store.has_session() and session.snapshot.current() is None, "the local tokens are gone (CA-20)")
    check(len(_token_calls()) == 1, "one question for the burst of refusals: {}".format(_token_calls()))

    # -- QGIS's raw error for that layer is taken back (CA-19) ---------------
    raw = _Item("Layer jardins (ALIGN)", "Download of features for layer {} failed or partially failed: Host requires authentication".format(LAYER))
    other = _Item("Layer roads", "Download of features for layer roads failed")
    iface.bar.widgetAdded.emit(raw)
    iface.bar.widgetAdded.emit(other)
    _wait(app, lambda: iface.bar.popped, 1)
    check(iface.bar.popped == [raw], "the ALIGN layer's raw error goes, any other message stays: {}".format(iface.bar.popped))

    # -- an edit saved after a revocation (CA-26) ----------------------------
    store.__init__()
    session.prime_signer()
    ended.clear()
    watch._checked_at = 0.0
    Recorder.token = TOKEN
    editable = service_layer.open_service(ref)
    check(editable.isValid() and editable.startEditing(), "in editing mode")
    feature = QgsFeature(editable.fields())
    feature.setAttribute("nom", "Jardin 4")
    check(editable.addFeature(feature), "a feature added to the buffer")
    Recorder.token = "a token ALIGN issued since"
    check(not editable.commitChanges(False), "the save is refused")
    check(editable.isEditable() and len(editable.editBuffer().addedFeatures()) == 1, "the change stays in QGIS's edit buffer")
    check(_wait(app, lambda: ended) and ended == [session_module.ENDED_BY_ALIGN], "and the session ends with the same message")
    editable.rollBack()

    # -- a project opened after a revocation made while QGIS was closed (CA-23)
    store.__init__()
    session.prime_signer()
    session._verified_at = time.time()
    ended.clear()
    Recorder.token = TOKEN
    project = QgsProject.instance()
    project.addMapLayer(service_layer.open_service(ref))
    folder = tempfile.mkdtemp()
    with_services = os.path.join(folder, "recipe.qgs")
    check(project.write(with_services), "the project writes")
    project.clear()
    plain = QgsVectorLayer("Point?crs=EPSG:2154", "roads", "memory")
    project.addMapLayer(plain)
    without = os.path.join(folder, "plain.qgs")
    project.write(without)
    project.clear()

    session._verified_at = 0.0
    Recorder.seen = []
    project.read(without)
    check(not _token_calls() and not ended, "a project without ALIGN service layers asks nothing")
    project.clear()

    Recorder.token = "a token ALIGN issued since"  # revoked while QGIS was closed
    Recorder.seen = []
    project.read(with_services)
    check(ended == [session_module.ENDED_BY_ALIGN], "the session was asked as the project opened, and ended")
    first_ogc = next((i for i, entry in enumerate(Recorder.seen) if SERVICE in entry[1]), len(Recorder.seen))
    first_token = next((i for i, entry in enumerate(Recorder.seen) if entry[1] == "POST /api/v1/oauth/token"), None)
    check(first_token is not None and first_token < first_ogc, "before the layers asked: {}".format(Recorder.seen[:4]))
    check(all(auth is None for (_p, path, auth) in Recorder.seen if SERVICE in path), "and nothing was signed with the revoked token")

    project.clear()
    watch.detach()
    signer.uninstall()
    del layer, editable, plain
    server.shutdown()
    config.set_endpoints(*previous)
    print("session watch inside QGIS {}: all checks passed".format(Qgis.QGIS_VERSION))
    app.exitQgis()


if __name__ == "__main__":
    main()
