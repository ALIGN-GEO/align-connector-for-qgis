#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The plugin's lot of feature services, inside QGIS (TCK-2346).

    python scripts/smoke_service_lot_qgis.py   (with QGIS' Python)

The plugin is loaded as QGIS loads it; the tasks are not run, their start is
recorded. What is proven:

* the layers of a lot that become feature services each send their file,
  and once the last has landed, ONE task asks for every service, with each
  layer's file and name;
* the checked layers as one service: each upload says which service it
  joins; a layer that could not be sent stops that service, and the message
  names it;
* one service per layer: a failed upload is left out, the others go;
* a raster of the same lot goes as a file, outside the services.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import os
import pathlib
import sys
from unittest.mock import MagicMock

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import Qgis, QgsApplication  # noqa: E402


def check(condition, message):
    if not condition:
        raise AssertionError(message)


class _Layer(object):
    def __init__(self, layer_id):
        self._id = layer_id

    def id(self):
        return self._id


class _Briefing(object):
    reference = None
    style = None
    iso = None


def _lot(batch_module, service_title, kinds):
    requests = []
    for index, kind in enumerate(kinds):
        plan = batch_module.Plan("layer-{}".format(index), "Layer {}".format(index), as_service=kind == "vector")
        requests.append(batch_module.Request(_Layer(plan.layer_id), plan, _Briefing()))
    return batch_module.Batch({"id": "org-1", "name": "Ville"}, "org", None, None, requests, service_title=service_title)


def main():
    app = QgsApplication([], False)
    app.initQgis()
    from qgis.PyQt.QtWidgets import QWidget

    from align_connector import classFactory, plugin as plugin_module
    from align_connector.panel import batch as batch_module

    iface = MagicMock()
    iface.mainWindow.return_value = QWidget()
    plugin = classFactory(iface)
    plugin.initGui()

    started = []
    uploads = []
    services = []

    class _Upload(object):
        def __init__(self, session, layer, org_id, org_name, title, **options):
            self.layer_id = layer.id()
            self.asset_name = title
            self.org_id = org_id
            self.org_name = org_name
            self.options = options
            self.asset_id = None
            self.needs_sign_in = False
            self.taskCompleted = MagicMock()
            self.taskTerminated = MagicMock()
            uploads.append(self)

    class _Services(object):
        def __init__(self, session, org_id, org_name, entries, **options):
            self.entries = entries
            self.options = options
            self.asset_name = "services"
            self.org_name = org_name
            self.taskCompleted = MagicMock()
            self.taskTerminated = MagicMock()
            services.append(self)

    plugin_module.PublishLayerTask = _Upload
    plugin_module.PublishServicesTask = _Services
    plugin_module.QgsApplication = MagicMock()
    plugin_module.QgsApplication.taskManager.return_value.addTask = started.append
    plugin.dock = MagicMock()
    messages = []
    plugin._message = lambda text, level: messages.append(text)

    def land(*succeeded):
        for upload, ok in zip(list(uploads), succeeded):
            upload.asset_id = "file-" + upload.layer_id if ok else None
            plugin._on_publish_finished(upload, ok)

    # The checked layers as one service, a raster in the same click.
    plugin._start_batch(_lot(batch_module, "Jardins", ["vector", "raster", "vector"]))
    check([u.options["joins_service"] for u in uploads] == ["Jardins", "Jardins", "Jardins"], "each upload knows its service")
    check([u.options["as_service"] for u in uploads] == [True, False, True], "the raster goes as a file")
    check(any("They will make one feature service, “Jardins”." in text for text in messages), messages)
    land(True, True)
    check(services == [], "nothing asked before the last service file has landed")
    land(True, True, True)
    check(len(services) == 1 and services[0].options["service_title"] == "Jardins", "one task for the one service")
    check(services[0].entries == [("layer-0", "file-layer-0", "Layer 0"), ("layer-2", "file-layer-2", "Layer 2")], services[0].entries)
    check(started[-1] is services[0], "and it was started")

    # A layer of the one service that could not be sent: no service (edge).
    del uploads[:], services[:], messages[:]
    plugin._start_batch(_lot(batch_module, "Jardins", ["vector", "vector"]))
    land(True, False)
    check(services == [], "nothing asked")
    check(any("The feature service “Jardins” was not created: “Layer 1” could not be sent." in text for text in messages), messages)

    # One service per layer: a failed upload is left out.
    del uploads[:], services[:], messages[:]
    plugin._start_batch(_lot(batch_module, None, ["vector", "vector", "vector"]))
    check([u.options["joins_service"] for u in uploads] == [None, None, None], "each layer its own service")
    land(True, False, True)
    check(len(services) == 1 and services[0].options["service_title"] is None, "one task, one request")
    check([entry[0] for entry in services[0].entries] == ["layer-0", "layer-2"], services[0].entries)

    plugin.unload()
    print("lot of feature services inside QGIS {}: all checks passed".format(Qgis.QGIS_VERSION))
    app.exitQgis()


if __name__ == "__main__":
    main()
