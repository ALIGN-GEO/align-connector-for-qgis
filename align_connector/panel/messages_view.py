# -*- coding: utf-8 -*-
"""The Messages view: every layer the panel refuses, with its reason.

A project of forty layers can hold ten the plugin will not send, each with
a sentence a GIS specialist acts on. Ten sentences under the layer list
leave no room for the list; here they have a view of their own, and the
Publish view keeps one line that points to it (TCK-2210).

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from ..qtcompat import qt_enum


class MessagesView(QWidget):
    """Rows of (layer, why), refreshed with the layer list; and the button
    that copies the diagnostic for a bug report (TCK-2219)."""

    def __init__(self, parent=None, on_copy_diagnostic=None):
        super().__init__(parent)
        #: ``[(layer name, reason)]`` as last shown, for the diagnostic.
        self.blocked = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.empty = QLabel(tr("Every layer of the project can be published."))
        self.empty.setWordWrap(True)
        self.empty.setEnabled(False)
        layout.addWidget(self.empty)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("Layer"), tr("Why it cannot be published")])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(qt_enum(QAbstractItemView, "SelectionMode", "NoSelection"))
        self.tree.setWordWrap(True)
        header = self.tree.header()
        header.setSectionResizeMode(0, qt_enum(QHeaderView, "ResizeMode", "ResizeToContents"))
        header.setSectionResizeMode(1, qt_enum(QHeaderView, "ResizeMode", "Stretch"))
        header.setStretchLastSection(True)
        self.tree.setVisible(False)
        layout.addWidget(self.tree, 1)

        # Reporting a problem: one click, and the report has what the support
        # needs (versions, addresses, the last refusals), never a token.
        foot = QHBoxLayout()
        hint = QLabel(tr("Something wrong? Copy the diagnostic and paste it in your report."))
        hint.setWordWrap(True)
        hint.setEnabled(False)
        foot.addWidget(hint, 1)
        self.diagnostic_button = QPushButton(tr("Copy diagnostic"))
        self.diagnostic_button.setFlat(True)
        self.diagnostic_button.setToolTip(
            tr("Versions, server addresses, session state and the last refusals, for a bug report. Never a token.")
        )
        if on_copy_diagnostic is not None:
            self.diagnostic_button.clicked.connect(lambda _checked=False: on_copy_diagnostic())
        foot.addWidget(self.diagnostic_button, 0)
        layout.addLayout(foot)

    def set_rows(self, rows):
        """Show the rows that cannot go (``batch.Row`` with a reason)."""
        blocked = [row for row in rows if not row.publishable]
        self.blocked = [(row.name, row.reason or "") for row in blocked]
        self.tree.clear()
        for row in blocked:
            item = QTreeWidgetItem([row.name, row.reason or ""])
            item.setToolTip(0, row.name)
            item.setToolTip(1, row.reason or "")
            self.tree.addTopLevelItem(item)
        self.tree.setVisible(bool(blocked))
        self.empty.setVisible(not blocked)
