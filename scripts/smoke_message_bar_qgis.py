#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove, on QGIS's own message bar, what the plugin takes back from it
(TCK-2296 defects 61 and 62).

    python scripts/smoke_message_bar_qgis.py

Needs the Python of a QGIS install, with its interface (offscreen). The
other scripts stand a fake bar in for QGIS's; this one pushes into a real
``QgsMessageBar`` the messages QGIS's application pushes itself
(``QgisApp::onLayerError``: "Layer <name>" and the provider's message;
``QgisApp::commitError``: "Could not commit changes to layer <name>"). What
is under test:

* after ALIGN ended the session, QGIS's raw errors for an ALIGN service
  layer are taken back, whether they name the layer's identifier (a failed
  download) or only its name (a refused change, a refused save); another
  layer's stay; signed in again, they stay too (CA-19, defect 61);
* a change ALIGN refuses for its rules (a 422 of a local server that stands
  for ALIGN): QGIS's two messages for the layer go, the refusal is
  explained once, the edit stays (CA-6, defect 62).

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

from qgis.core import Qgis, QgsApplication, QgsVectorLayer  # noqa: E402

from ogc_stub import LAYER, SERVICE, Recorder, serve  # noqa: E402


def check(condition, message):
    if not condition:
        raise AssertionError(message)


class _Iface(object):
    def __init__(self, bar):
        self.bar = bar

    def messageBar(self):  # noqa: N802 — QGIS's name
        return self.bar


class _Session(object):
    def __init__(self, signed_in):
        self.signed_in = signed_in

    def is_signed_in(self):
        return self.signed_in


def _settle(app):
    for _ in range(5):
        app.processEvents()
        time.sleep(0.02)


def _left(bar):
    return sorted((item.title(), item.text()) for item in bar.items())


def test_raw_errors_after_a_revocation_are_taken_back(app, bar):
    from align_connector.publish import service_memory
    from align_connector.session_watch import SessionWatch

    layer = QgsVectorLayer("Point?crs=EPSG:4326", "Jardins (ALIGN, read-only)", "memory")
    ref = service_memory.ServiceRef("org-1", "svc-jardins", LAYER, "Jardins", None, service_memory.SERVICE)
    session = _Session(signed_in=False)
    watch = SessionWatch(_Iface(bar), session, lambda: [(layer, ref)])
    watch.attach()
    try:
        title = "Layer Jardins (ALIGN, read-only)"
        bar.pushCritical(title, "Download of features for layer {} failed or partially failed: Host requires authentication.".format(LAYER))
        bar.pushCritical(title, "Cannot modify feature of id 1")
        bar.pushMessage("Commit errors", "Could not commit changes to layer Jardins (ALIGN, read-only)", Qgis.MessageLevel.Warning)
        bar.pushCritical("Layer roads", "Download of features for layer roads failed or partially failed")
        _settle(app)
        check(_left(bar) == [("Layer roads", "Download of features for layer roads failed or partially failed")], "only another layer's error stays: {}".format(_left(bar)))

        session.signed_in = True
        bar.pushCritical(title, "The layer extent reported by the server is not correct.")
        _settle(app)
        check(any("extent" in text for _title, text in _left(bar)), "signed in again, QGIS's messages stay: {}".format(_left(bar)))
    finally:
        watch.detach()
        bar.clearWidgets()


def test_a_rules_refusal_is_taken_back_from_the_real_bar(app, bar):
    from align_connector import config
    from align_connector.publish import service_form, service_memory
    from align_connector.session_watch import SessionWatch

    server = serve()
    previous = (config.app_url(), config.api_url())
    config.set_endpoints(previous[0], "http://127.0.0.1:{}".format(server.server_address[1]))
    Recorder.seen, Recorder.require_token = [], False
    Recorder.items_answer = (422, {"code": "InvalidFeature", "description": "nom: Needs at least 3 letters"})
    url = "http://127.0.0.1:{}{}".format(server.server_address[1], SERVICE)
    layer = QgsVectorLayer("url='{}' typename='{}'".format(url, LAYER), "Jardins (ALIGN)", "OAPIF")
    refused = []
    watch = SessionWatch(_Iface(bar), _Session(signed_in=True), lambda: [(layer, ref)], on_rules_refusal=lambda refused_layer, answer: refused.append((refused_layer, answer)))
    try:
        check(layer.isValid(), "the service layer opens")
        rule = {"field": "nom", "widget": "text", "constraintExpression": '[">=", ["length", ["get", "nom"]], 3]', "constraintDescription": "Needs at least 3 letters"}
        service_form.apply_form(layer, {"fields": [{"name": "nom", "type": "string"}], "form": {"fields": [rule]}})
        ref = service_memory.ServiceRef("org-1", "svc-jardins", LAYER, "Jardins", None, service_memory.SERVICE)
        watch.attach()
        check(layer.startEditing(), "in editing mode")
        feature = next(layer.getFeatures())
        layer.changeAttributeValue(feature.id(), layer.fields().indexOf("nom"), "ab")
        check(not layer.commitChanges(False), "ALIGN refuses the save")
        # What QGIS's application pushes for that save, as it does it.
        bar.pushCritical("Layer Jardins (ALIGN)", layer.commitErrors()[-1].strip())
        bar.pushMessage("Commit errors", "Could not commit changes to layer Jardins (ALIGN)", Qgis.MessageLevel.Warning)
        bar.pushCritical("Layer roads", "Cannot modify feature of id 1")
        _settle(app)
        check(_left(bar) == [("Layer roads", "Cannot modify feature of id 1")], "QGIS's messages for the ALIGN layer go, another layer's stays: {}".format(_left(bar)))
        check([entry[0] for entry in refused] == [layer], "explained once: {}".format(refused))
        check(layer.isEditable() and layer.editBuffer().changedAttributeValues(), "the change stays, to be corrected")
        layer.rollBack()
    finally:
        watch.detach()
        bar.clearWidgets()
        Recorder.items_answer = None
        config.set_endpoints(*previous)
        server.shutdown()


def main():
    app = QgsApplication([], True)
    app.initQgis()
    from qgis.gui import QgsMessageBar

    bar = QgsMessageBar()
    bar.show()
    tests = [test_raw_errors_after_a_revocation_are_taken_back, test_a_rules_refusal_is_taken_back_from_the_real_bar]
    for test in tests:
        test(app, bar)
        print("ok  {}".format(test.__name__))
    print("message bar inside QGIS {}: {} checks passed".format(Qgis.QGIS_VERSION, len(tests)))
    app.exitQgis()


if __name__ == "__main__":
    main()
