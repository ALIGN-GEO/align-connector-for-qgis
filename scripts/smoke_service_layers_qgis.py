#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The feature-service layers inside QGIS, end to end (TCK-2348).

    python scripts/smoke_service_layers_qgis.py   (with QGIS' Python, 3.36 or later)

A local server stands for ALIGN's OGC facade and answers 401 to an unsigned
request, as ALIGN does. The plugin's service actions are attached to a real
QGIS project and layer tree (the interface is a stand-in). What is proven:

* "Edit in QGIS" on a published original opens the service in its place,
  named "<title> (ALIGN)", the original kept hidden in "Local originals"
  (CA-7); the service layer is editable, and every request of it was signed;
* a layer opened read-only stays read-only;
* both the original and the service layer get the context-menu entry (CA-6);
* the project, written to disk, holds no authcfg and no token (CA-8);
* a project read without a session says "Sign in to ALIGN to read…" with a
  button, and the sign-in reads the layer again (CA-11);
* the service layer opens with the service's input rules, read without the
  per-layer counts (TCK-2343);
* detaching takes back the signer and the menu entries.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import os
import pathlib
import sys
import tempfile
import time
from unittest.mock import MagicMock

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import Qgis, QgsApplication, QgsProject, QgsVectorLayer  # noqa: E402
from qgis.PyQt.QtWidgets import QWidget  # noqa: E402

from ogc_stub import LAYER, TOKEN, Recorder, serve  # noqa: E402


def check(condition, message):
    if not condition:
        raise AssertionError(message)


class _Session(object):
    """The plugin's session, as the actions see it: a snapshot and a freshness."""

    def __init__(self):
        from align_connector.auth.request_signer import TokenSnapshot

        self.snapshot = TokenSnapshot()
        self.live = True
        self.signed_in = MagicMock()
        self.sign_in = MagicMock()

    def prime_signer(self):
        if self.live:
            self.snapshot.set(TOKEN, time.time() + 3600)

    def keep_signer_fresh(self):
        if not self.live:
            self.snapshot.clear()
            return False
        self.snapshot.set(TOKEN, time.time() + 3600)
        return True

    def is_signed_in(self):
        return self.live

    def verify(self):
        return self.keep_signer_fresh()

    def verify_if_stale(self):
        return self.keep_signer_fresh()


def main():
    app = QgsApplication([], False)
    app.initQgis()
    if Qgis.QGIS_VERSION_INT < 33600:
        print("service layers: needs QGIS 3.36 or later, skipped on {}".format(Qgis.QGIS_VERSION))
        app.exitQgis()
        return

    server = serve()
    Recorder.seen, Recorder.require_token = [], True
    api_url = "http://127.0.0.1:{}".format(server.server_address[1])
    from align_connector import config

    previous = (config.app_url(), config.api_url())
    config.set_endpoints(previous[0], api_url)
    from align_connector import service_actions as actions_module
    from align_connector.publish import service_layer, service_memory

    check(config.api_url() == api_url, "the plugin points at the local server")
    form = {"fields": [{"field": "nom", "widget": "text", "required": True, "alias": "Parcel name"}]}
    describe = {"callerMay": ["add", "update", "delete"], "layers": [{"layerId": LAYER, "fields": [{"name": "nom", "type": "string"}], "form": form}]}
    asked_counts = []

    def describe_service(_session, _org, _service, counts=True):
        asked_counts.append(counts)
        return describe

    actions_module.describe_service = describe_service

    iface = MagicMock()
    iface.mainWindow.return_value = QWidget()
    session = _Session()
    messages = []
    actions = actions_module.ServiceLayerActions(iface, session, lambda text, level: messages.append(text))
    actions.attach()
    check(iface.addCustomActionForLayerType.call_count == 2, "two context-menu entries: edit and read")

    project = QgsProject.instance()
    original = QgsVectorLayer("Point?crs=EPSG:2154&field=nom:string", "parcelles", "memory")
    project.addMapLayer(original)
    from align_connector.publish import memory as datastore_memory

    datastore_memory.remember(original, "asset-1", "org-1", org_name="Ville", title="parcelles")
    ref = service_memory.ServiceRef("org-1", "svc-jardins", LAYER, "parcelles", None, service_memory.ORIGINAL)
    service_memory.remember(original, ref, service_memory.ORIGINAL)
    actions._offer(original)

    actions.edit_layer(original)
    opened = [layer for layer, _ref in service_layer.service_layers()]
    check(len(opened) == 1, "the service opened: {}".format(messages))
    layer = opened[0]
    check(layer.isValid() and layer.name() == "parcelles (ALIGN)", (layer.isValid(), layer.name()))
    check(layer.name() != original.name(), "never the original's name (CA-7)")
    check(service_memory.read(layer).kind == service_memory.SERVICE, "the service layer is the service's, not a copy of the original's memory")
    from align_connector.publish import memory

    check(memory.read(layer) is None, "nor the Datastore item the original was published as")
    check(layer.isEditable() and not layer.readOnly(), "in editing mode")
    check(layer.attributeDisplayName(layer.fields().indexOf("nom")) == "Parcel name", "with the service's input rules (TCK-2343)")
    check(asked_counts and not any(asked_counts), "read without the per-layer counts: {}".format(asked_counts))
    signed = [auth for (_p, path, auth) in Recorder.seen if path.startswith("/api/v1/orgs/org-1/feature-services/svc-jardins/ogc")]
    check(signed and all(auth == "Bearer " + TOKEN for auth in signed), "every request was signed")
    group = project.layerTreeRoot().findGroup("Local originals")
    node = group.findLayer(original.id()) if group else None
    check(node is not None and not node.itemVisibilityChecked(), "the original is kept, hidden, in “Local originals”")
    layer.rollBack()
    offered = [call[0][1] for call in iface.addCustomActionForLayer.call_args_list]
    check(original in offered and layer in offered, "both layers offer the service in their context menu (CA-6)")

    read_ref = service_memory.ServiceRef("org-1", "svc-jardins", LAYER, "jardins", None, service_memory.SERVICE)
    read_only = actions.open(read_ref, read_only=True)
    check(read_only is not None and read_only.readOnly(), "opened read-only, it stays read-only")
    check(read_only.name() == "jardins (ALIGN, read-only)", "and says so in its name (CA-13): {}".format(read_only.name()))
    check(len(list(read_only.getFeatures())) == 3, "and reads its features")

    path = os.path.join(tempfile.mkdtemp(), "recipe.qgs")
    check(project.write(path), "the project writes")
    written = open(path, encoding="utf-8").read()
    check("authcfg" not in written and TOKEN not in written and "Bearer" not in written, "the project holds no credential (CA-8)")

    # A project read without a session (CA-11).
    session.live = False
    session.snapshot.clear()
    unsigned = QgsVectorLayer(layer.source(), "jardins (ALIGN)", "OAPIF")
    check(not unsigned.isValid(), "without a session, ALIGN refuses the layer")
    service_memory.remember(unsigned, read_ref, service_memory.SERVICE)
    project.addMapLayer(unsigned)
    actions._on_project_read()
    pushed = [str(call[0][1]) for call in iface.messageBar().createMessage.call_args_list]
    check(any("Sign in to ALIGN to read" in text for text in pushed), pushed)
    session.live = True
    actions._on_signed_in()
    check(unsigned.isValid(), "after the sign-in, the layer reads (CA-11)")

    actions.detach()
    check(iface.removeCustomActionForLayerType.call_count == 2, "detach takes the menu entries back")
    Recorder.seen = []
    from qgis.core import QgsBlockingNetworkRequest
    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtNetwork import QNetworkRequest

    session.snapshot.set(TOKEN, time.time() + 3600)
    QgsBlockingNetworkRequest().get(QNetworkRequest(QUrl(api_url + "/api/v1/orgs/org-1/feature-services/svc-jardins/ogc")))
    check(all(auth is None for (_p, _path, auth) in Recorder.seen), "and the signer")

    project.clear()
    del layer, read_only, unsigned, original
    server.shutdown()
    config.set_endpoints(*previous)
    print("service layers inside QGIS {}: all checks passed".format(Qgis.QGIS_VERSION))
    app.exitQgis()


if __name__ == "__main__":
    main()
