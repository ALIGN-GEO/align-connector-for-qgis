# -*- coding: utf-8 -*-
"""The Messages view: every layer the panel refuses, with its reason.

A project of forty layers can hold ten the plugin will not send, each with
a sentence a GIS specialist acts on. Ten sentences under the layer list
leave no room for the list; here they have a view of their own, and the
Publish view keeps one line that points to it (TCK-2210).

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from qgis.PyQt.QtWidgets import QAbstractItemView, QHeaderView, QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from ..i18n import tr
from ..qtcompat import qt_enum


class MessagesView(QWidget):
    """Rows of (layer, why), refreshed with the layer list."""

    def __init__(self, parent=None):
        super().__init__(parent)
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

    def set_rows(self, rows):
        """Show the rows that cannot go (``batch.Row`` with a reason)."""
        blocked = [row for row in rows if not row.publishable]
        self.tree.clear()
        for row in blocked:
            item = QTreeWidgetItem([row.name, row.reason or ""])
            item.setToolTip(0, row.name)
            item.setToolTip(1, row.reason or "")
            self.tree.addTopLevelItem(item)
        self.tree.setVisible(bool(blocked))
        self.empty.setVisible(not blocked)
