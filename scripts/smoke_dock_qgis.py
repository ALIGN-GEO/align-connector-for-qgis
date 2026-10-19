#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove, inside QGIS, what the ALIGN panel shows (TCK-2348, patch 0003).

    python scripts/smoke_dock_qgis.py

The real panel, with ALIGN's answers given in-process. What is under test:

* an organization the plugin may not use stays listed, greyed, its reason in
  the tooltip; the selection goes to a usable one (CA-17);
* one organization for both tabs: each follows the other where it may (CA-24);
* the Services tab keeps the service in hand through a refresh, and the ALIGN
  button keeps the tab in use when the panel is already open (CA-14);
* a sign-in waiting in the browser shows "Signing in from the browser…" with
  Copy link and Cancel (CA-25);
* signed out, the panel keeps nothing it read for the account: no organization,
  no service, no service sheet (CA-20).

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import os
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import Qgis, QgsApplication, QgsProject, QgsVectorLayer  # noqa: E402
from qgis.PyQt.QtCore import QObject, Qt, pyqtSignal  # noqa: E402
from qgis.PyQt.QtWidgets import QApplication  # noqa: E402


def check(condition, message):
    if not condition:
        raise AssertionError(message)


ORGS = [
    {"id": "o-closed", "name": "A closed", "role": "member", "connector": {"allowed": False, "reason": "org-off", "featureServices": True}},
    {"id": "o-files", "name": "B files only", "role": "member", "connector": {"allowed": True, "featureServices": False}},
    {"id": "o-team", "name": "C team", "role": "member", "connector": {"allowed": True, "featureServices": True}},
    {"id": "o-second", "name": "D second team", "role": "member", "connector": {"allowed": True, "featureServices": True}},
]
SERVICES = [
    {"serviceId": "s-1", "title": "Arbres", "canEdit": True},
    {"serviceId": "s-2", "title": "Jardins", "canEdit": True},
    {"serviceId": "s-3", "title": "Réseau", "canEdit": False},
]


class _Session(QObject):
    signed_in = pyqtSignal()
    signed_out = pyqtSignal()
    failed = pyqtSignal(str)
    waiting_for_browser = pyqtSignal()
    pending_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.pending_url = None
        self.cancelled = 0
        self.signed = True

    def is_signed_in(self):
        return self.signed

    def account(self):
        return "marie@ville.fr"

    def has_scope(self, _scope):
        return True

    def cancel_sign_in(self):
        self.cancelled += 1
        self.pending_url = None
        self.pending_changed.emit()


def _enabled(box, org_id):
    return box.model().item(box.findData(org_id)).isEnabled()


def _tooltip(box, org_id):
    return box.itemData(box.findData(org_id), Qt.ItemDataRole.ToolTipRole) or ""


def main():
    app = QgsApplication([], True)
    app.initQgis()
    from align_connector import api
    from align_connector.panel import dock as dock_module, services_view

    api.list_organizations = lambda session: [dict(org) for org in ORGS]
    api.capacity = lambda session, org: {"storage": {"usedBytes": 1, "limitBytes": 10}, "credits": {"available": 5}, "featureServices": True}
    listed = []
    services_view.list_services = lambda session, org: listed.append(org) or {"services": SERVICES, "truncated": False}
    services_view.describe_service = lambda session, org, sid: {"serviceId": sid, "callerMay": ["add"], "layers": []}

    session = _Session()
    dock = dock_module.AlignDock(session, lambda batch: None)
    dock.show_panel()
    publish, services = dock.publish, dock.services

    # -- greyed, with the reason (CA-17) -------------------------------------
    check(not _enabled(publish.org_box, "o-closed") and "Settings → Integrations" in _tooltip(publish.org_box, "o-closed"), "Publish: a closed organization is greyed, and says who can open it")
    check(_enabled(publish.org_box, "o-files") and _enabled(publish.org_box, "o-team"), "Publish: files may go to an organization without services")
    check(publish.org_box.currentData() == "o-files", "the selection goes to the first usable one, never a greyed one")
    dock.tabs.setCurrentWidget(services)
    app.processEvents()
    check(not _enabled(services.org_box, "o-files") and "plan" in _tooltip(services.org_box, "o-files"), "Services: an organization without services is greyed, with the reason")
    check(services.org_box.currentData() == "o-team", "Services opens on the one that has services")

    # -- one organization for both tabs (CA-24) -------------------------------
    check(publish.org_box.currentData() == "o-team", "Publish followed the Services tab")
    publish.org_box.setCurrentIndex(publish.org_box.findData("o-files"))
    check(services.org_box.currentData() == "o-team", "Services does not follow to an organization it may not use (edge)")

    # -- the service in hand stays, and so does the tab (CA-14) --------------
    services.list.setCurrentItem(services.list.topLevelItem(2))
    check(services._selected_service()["serviceId"] == "s-3", "the third service in hand")
    services.refresh()
    check(services._selected_service()["serviceId"] == "s-3", "still in hand after a refresh")
    dock.show_panel()
    check(dock.tabs.currentWidget() is services, "the ALIGN button keeps the tab in use when the panel is open")

    # -- a project opened: the Publish tab lists its layers (defect 67) -------
    folder = tempfile.mkdtemp()
    data = os.path.join(folder, "parcelles.geojson")
    with open(data, "w", encoding="utf-8") as handle:
        handle.write('{"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"nom": "a"}, "geometry": {"type": "Point", "coordinates": [4.35, 50.85]}}]}')
    project = QgsProject.instance()
    project.addMapLayer(QgsVectorLayer(data, "parcelles", "ogr"))
    saved = os.path.join(folder, "recipe.qgs")
    check(project.write(saved), "the project writes")
    project.clear()
    app.processEvents()
    check(publish.tree.topLevelItemCount() == 0, "a cleared project lists nothing")
    check(project.read(saved), "the project opens")
    app.processEvents()
    names = [publish.tree.topLevelItem(i).text(0) for i in range(publish.tree.topLevelItemCount())]
    check(names == ["parcelles"], "the opened project's layers are listed in Publish: {}".format(names))
    project.clear()

    # -- a sign-in waiting in the browser (CA-25) -----------------------------
    check(not dock.pending.isVisibleTo(dock), "nothing waits: no row")
    session.pending_url = "https://app.example/oauth/authorize?client_id=align-connector-qgis&state=s"
    session.pending_changed.emit()
    check(dock.pending.isVisibleTo(dock), "waiting: the row shows")
    dock.copy_link_button.click()
    check(QApplication.clipboard().text() == session.pending_url, "Copy link: the address to finish in another browser")
    dock.cancel_button.click()
    check(session.cancelled == 1 and not dock.pending.isVisibleTo(dock), "Cancel gives the attempt up, and the row goes")

    # -- signed out, nothing of the account stays on screen (CA-20) ----------
    publish.org_box.setCurrentIndex(publish.org_box.findData("o-second"))
    session.signed = False
    dock.refresh(reload=False)  # what the plugin does on signed_out
    check(dock.account.text() == "Not connected to ALIGN", "the header says so")
    check(publish.org_box.count() == 0 and services.org_box.count() == 0, "no organization left listed")
    check(services.list.topLevelItemCount() == 0 and not services.sheet_box.isVisibleTo(services), "no service, no sheet")
    check("Sign in" in services.note.text(), "the Services tab says what to do: {}".format(services.note.text()))
    check(publish.organizations() is None, "the diagnostic says the organizations were not read, not 'none'")

    # -- signed in again, the organization chosen before is kept (finding O) --
    session.signed = True
    dock.refresh(reload=True)
    app.processEvents()
    check(publish.org_box.currentData() == "o-second", "Publish is back on the organization chosen before: {}".format(publish.org_box.currentData()))
    dock.tabs.setCurrentWidget(services)
    services.refresh()
    check(services.org_box.currentData() == "o-second", "and so is the Services tab: {}".format(services.org_box.currentData()))

    dock.deleteLater()
    print("panel inside QGIS {}: all checks passed".format(Qgis.QGIS_VERSION))
    app.exitQgis()


if __name__ == "__main__":
    main()
