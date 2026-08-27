# -*- coding: utf-8 -*-
"""Plugin lifecycle: what ALIGN Connector adds to QGIS, and what it takes back.

This module owns *only* the lifecycle. Authentication, publishing and dialogs
live in their own modules — keeping this one small is what makes the plugin
reloadable during development without leaving orphaned menu entries behind.

Two QGIS rules shape the code below and are worth stating, because breaking
either is invisible until it hurts:

* Everything added in :meth:`initGui` must be removed in :meth:`unload`. QGIS
  calls ``unload`` on every plugin reload; an entry that is not removed is
  duplicated on the next load, and the stale one points at a dead object.
* Imports come from ``qgis.PyQt``, never from ``PyQt5`` directly. That shim is
  what lets the same source run on a Qt5 build (QGIS 3.44 LTR, our reference)
  and on a Qt6 build without a fork.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import os

from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

#: Menu title under QGIS' "Web" menu. Kept in one place: it is both what we add
#: and what we must remove, and the two must never drift apart.
MENU_TITLE = "&ALIGN"

PLUGIN_NAME = "ALIGN Connector"


class AlignConnectorPlugin:
    """The object QGIS instantiates, keeps, and calls back into."""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        #: Every QAction we registered, so unload can undo exactly this much.
        self._actions = []

    # -- i18n ---------------------------------------------------------------

    @staticmethod
    def tr(message):
        """Translate a UI string.

        Wired from the start even though no translation is shipped yet: adding
        it later means revisiting every string, and QGIS users expect their
        own language.
        """
        return QCoreApplication.translate("AlignConnector", message)

    # -- lifecycle ----------------------------------------------------------

    def initGui(self):  # noqa: N802 — name imposed by the QGIS plugin API
        """Add the plugin's entry points to the QGIS interface."""
        action = QAction(
            QIcon(os.path.join(self.plugin_dir, "icon.svg")),
            self.tr("Publish to ALIGN…"),
            self.iface.mainWindow(),
        )
        action.setStatusTip(self.tr("Publish the selected layer to your ALIGN Datastore"))
        action.triggered.connect(self.run)

        self.iface.addPluginToWebMenu(MENU_TITLE, action)
        self.iface.addToolBarIcon(action)
        self._actions.append(action)

    def unload(self):
        """Remove everything :meth:`initGui` added — no more, no less."""
        for action in self._actions:
            self.iface.removePluginWebMenu(MENU_TITLE, action)
            self.iface.removeToolBarIcon(action)
        self._actions.clear()

    # -- entry point --------------------------------------------------------

    def run(self):
        """Open the publish flow.

        Placeholder until the sign-in flow lands (TCK-1661) and publishing
        follows (TCK-1662). It states plainly what is not built yet rather than
        opening an empty dialog: a control that pretends to work is worse than
        one that says it does not.
        """
        self.iface.messageBar().pushInfo(
            PLUGIN_NAME,
            self.tr("Sign-in and publishing are not wired yet — coming in the next release."),
        )
