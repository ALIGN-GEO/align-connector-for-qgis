# -*- coding: utf-8 -*-
"""The ALIGN panel: a dock in QGIS, at its place (ADR-0058 §5).

"Beautiful" in QGIS means at home: QGIS' own dock widget, QGIS' own widgets,
the user's theme and fonts, the keyboard. The brand lives in the name, the
icon and the tone of the texts, and in the link that takes everything QGIS
should not redo back to the web.

Two views today, Publish and Activity; the Datastore view waits for the
read scope of the roadmap's v2.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from qgis.gui import QgsDockWidget
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTabWidget, QVBoxLayout, QWidget

from ..i18n import tr
from ..qtcompat import qt_enum
from .activity_view import ActivityView
from .publish_view import PublishView

#: What QGIS saves the panel's place under. Changing it loses that place.
OBJECT_NAME = "AlignConnectorDock"


class AlignDock(QgsDockWidget):
    """The panel QGIS keeps, docks and remembers (by object name)."""

    def __init__(self, session, on_publish, parent=None):
        super().__init__("ALIGN", parent)
        self.setObjectName(OBJECT_NAME)
        self.session = session

        body = QWidget()
        layout = QVBoxLayout(body)

        # -- who ---------------------------------------------------------------
        head = QHBoxLayout()
        self.account = QLabel()
        self.account.setWordWrap(True)
        head.addWidget(self.account, 1)
        self.sign_button = QPushButton()
        self.sign_button.setFlat(True)
        self.sign_button.clicked.connect(self._toggle_sign_in)
        head.addWidget(self.sign_button, 0)
        layout.addLayout(head)

        # -- the views --------------------------------------------------------
        self.tabs = QTabWidget()
        self.publish = PublishView(session, on_publish)
        self.activity = ActivityView()
        self.tabs.addTab(self.publish, tr("Publish"))
        self.tabs.addTab(self.activity, tr("Activity"))
        layout.addWidget(self.tabs, 1)

        self.setWidget(body)
        self._refresh_account()

    # -- lifecycle ----------------------------------------------------------

    def refresh(self, reload=True):
        """Make the panel say the truth about the session; with ``reload``,
        also read the organizations and the project again."""
        self._refresh_account()
        if reload and self.session.is_signed_in():
            self.publish.refresh()

    def show_panel(self):
        """Show the panel, raise its tab, and read everything afresh."""
        self.setUserVisible(True)
        self.refresh(reload=True)
        self.tabs.setCurrentWidget(self.publish)

    def detach(self):
        """Stop following the project. Called before the plugin unloads."""
        self.publish.detach()

    # -- publications -------------------------------------------------------

    def track(self, task, org_id):
        self.activity.start(task, org_id)

    def settle(self, task, outcome):
        self.activity.finish(task, outcome)

    def show_activity(self):
        self.tabs.setCurrentWidget(self.activity)

    # -- session ------------------------------------------------------------

    def _refresh_account(self):
        signed_in = self.session.is_signed_in()
        account = self.session.account() if signed_in else None
        if account:
            text = tr("Connected as {}").format(account)
        elif signed_in:
            text = tr("Connected to ALIGN")
        else:
            text = tr("Not connected to ALIGN")
        self.account.setText(text)
        self.sign_button.setText(tr("Sign out") if signed_in else tr("Sign in"))
        self.publish.setEnabled(signed_in)

    def _toggle_sign_in(self):
        if self.session.is_signed_in():
            self.session.sign_out()
        else:
            self.session.sign_in()


def dock_area():
    """Where the panel first appears: on the right, next to the map."""
    return qt_enum(Qt, "DockWidgetArea", "RightDockWidgetArea")
