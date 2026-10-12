# -*- coding: utf-8 -*-
"""The Activity view: every publication, where it stands, how it ended.

A transfer takes minutes, sometimes hours. Three hours without a signal is an
application that looks frozen, so each publication has a row that moves
while it runs and says, when it ends, what ALIGN made of it, in the same
words as the message bar, with a link to the item on ALIGN. A web map built
from a lot gets the same row, with its own link.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import config
from ..diagnostic import MAX_HISTORY
from ..i18n import tr
from ..qtcompat import qt_enum
from .outcome import SUCCESS


class _PublicationRow(QFrame):
    """One publication: where it goes, how far it is, how it ended."""

    def __init__(self, title, org_name, busy=False, parent=None):
        super().__init__(parent)
        self.title = title
        self.org_name = org_name
        self.setFrameShape(qt_enum(QFrame, "Shape", "StyledPanel"))
        layout = QVBoxLayout(self)

        head = QLabel("{} → {}".format(title, org_name))
        head.setWordWrap(True)
        font = head.font()
        font.setBold(True)
        head.setFont(font)
        layout.addWidget(head)

        self.bar = QProgressBar()
        # A request with no measurable progress shows a busy bar, not a stuck one.
        self.bar.setRange(0, 0 if busy else 100)
        self.bar.setValue(0)
        layout.addWidget(self.bar)

        foot = QHBoxLayout()
        self.status = QLabel(tr("Waiting for ALIGN…") if busy else tr("Preparing…"))
        self.status.setWordWrap(True)
        foot.addWidget(self.status, 1)
        self.open_button = QPushButton(tr("Open in ALIGN"))
        self.open_button.setFlat(True)
        self.open_button.setVisible(False)
        foot.addWidget(self.open_button, 0)
        layout.addLayout(foot)

        self.finished = False

    def set_progress(self, progress):
        self.bar.setValue(int(progress))
        if progress >= 100:
            self.status.setText(tr("Cataloguing…"))
        elif progress > 0:
            self.status.setText(tr("Uploading…"))

    def finish(self, outcome, url=None):
        self.finished = True
        self.bar.setVisible(False)
        self.status.setText(outcome.text)
        self.status.setToolTip(outcome.text)
        if url:
            self.open_button.setVisible(True)
            self.open_button.clicked.connect(lambda _checked=False, target=url: QDesktopServices.openUrl(QUrl(target)))


class ActivityView(QWidget):
    """Rows of publications, living and finished, newest first."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.empty = QLabel(tr("No publication yet. Pick layers in the Publish view."))
        self.empty.setWordWrap(True)
        self.empty.setEnabled(False)
        layout.addWidget(self.empty)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(qt_enum(QFrame, "Shape", "NoFrame"))
        self.scroll.setVisible(False)
        container = QWidget()
        self._rows_layout = QVBoxLayout(container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.addStretch(1)
        self.scroll.setWidget(container)
        layout.addWidget(self.scroll, 1)

        self.clear_button = QPushButton(tr("Clear finished"))
        self.clear_button.setFlat(True)
        self.clear_button.setVisible(False)
        self.clear_button.clicked.connect(self.clear_finished)
        layout.addWidget(self.clear_button)

        #: task → (row, org id, link builder). Keyed by the task object: the
        #: row must be found again from the task's own signals and outcome.
        self._rows = {}
        #: The last finished publications, newest first, for the diagnostic
        #: (TCK-2219): title, organization, level, sentence, ALIGN's status
        #: and code. Bounded; survives "Clear finished".
        self.history = []

    def start(self, task, org_id, title=None, link=None, busy=False):
        """A publication just left: give it a row that moves.

        :param link: builds the "Open in ALIGN" URL from ``(org_id, asset_id)``;
            the item's page by default
        """
        row = _PublicationRow(title or task.asset_name, task.org_name, busy=busy)
        self._rows_layout.insertWidget(0, row)
        self._rows[task] = (row, org_id, link or config.item_url)
        self.empty.setVisible(False)
        self.scroll.setVisible(True)
        # A bound method, not a lambda: Qt drops the connection when this
        # view is destroyed, so a task that outlives the panel never calls
        # into a deleted widget.
        task.progressChanged.connect(self._on_progress)

    def finish(self, task, outcome):
        """The publication ended: say what happened, and offer the link."""
        entry = self._rows.get(task)
        if entry is None:
            return
        row, org_id, link = entry
        url = link(org_id, outcome.asset_id) if outcome.level == SUCCESS and outcome.asset_id else None
        row.finish(outcome, url)
        self.history.insert(0, {
            "title": row.title,
            "org": row.org_name,
            "level": outcome.level,
            "text": outcome.text,
            "status": getattr(task, "error_status", None),
            "code": getattr(task, "error_code", None),
        })
        del self.history[MAX_HISTORY:]
        try:
            task.progressChanged.disconnect(self._on_progress)
        except (TypeError, RuntimeError):
            pass
        self.clear_button.setVisible(True)

    def clear_finished(self):
        for task, (row, _org_id, _link) in list(self._rows.items()):
            if row.finished:
                self._rows_layout.removeWidget(row)
                row.setParent(None)
                row.deleteLater()
                del self._rows[task]
        if not self._rows:
            self.scroll.setVisible(False)
            self.empty.setVisible(True)
        self.clear_button.setVisible(any(row.finished for row, _, _ in self._rows.values()))

    def _on_progress(self, progress):
        entry = self._rows.get(self.sender())
        if entry is not None and entry[0].bar.maximum() > 0:
            entry[0].set_progress(progress)
