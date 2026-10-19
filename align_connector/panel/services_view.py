# -*- coding: utf-8 -*-
"""The Services tab: the organization's feature services (TCK-2348).

ArcGIS Pro shows a portal's catalogue; this shows the organization's feature
services the user may read (CA-3), searched by name, each with its sheet and
its layers (CA-4), and opens a layer for reading or for editing (CA-5). ALIGN
decides who may edit: a service the user may not edit has no edit button.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import config
from ..api import ApiError
from ..i18n import tr
from ..qtcompat import qt_enum
from ..services_api import describe_service, list_services
from . import org_choice, services_model


class ServicesView(QWidget):
    """One organization's services, a sheet, and the layers to open."""

    def __init__(self, session, organizations, on_open, on_org_chosen=None, parent=None):
        """
        :param organizations: ``() -> [{"id", "name", "role", "connector"}]``, the
            organizations the Publish tab loaded (one read for both tabs)
        :param on_open: ``(org_id, service, layer_row, edit) -> None``
        :param on_org_chosen: called with the organization id chosen here (the Publish tab follows)
        """
        super().__init__(parent)
        self.session = session
        self._organizations = organizations
        self._on_open = on_open
        self._on_org_chosen = on_org_chosen
        self._services = []
        self._description = None

        layout = QVBoxLayout(self)

        head = QHBoxLayout()
        self.org_box = QComboBox()
        self.org_box.currentIndexChanged.connect(self._on_org_changed)
        head.addWidget(self.org_box, 1)
        self.refresh_button = QPushButton(tr("Refresh"))
        self.refresh_button.clicked.connect(self.refresh)
        head.addWidget(self.refresh_button, 0)
        layout.addLayout(head)

        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search the services by name"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._fill_list)
        layout.addWidget(self.search)

        self.list = QTreeWidget()
        self.list.setHeaderLabels([tr("Service"), tr("Access")])
        self.list.setRootIsDecorated(False)
        _fit(self.list, stretch=0)
        self.list.currentItemChanged.connect(self._on_service_changed)
        layout.addWidget(self.list, 2)

        self.note = QLabel()
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

        self.sheet_box = QGroupBox(tr("Service"))
        self.sheet = QFormLayout(self.sheet_box)
        layout.addWidget(self.sheet_box, 0)

        self.layers = QTreeWidget()
        self.layers.setHeaderLabels([tr("Layer"), tr("Geometry"), tr("Features"), tr("Fields")])
        self.layers.setRootIsDecorated(False)
        _fit(self.layers, stretch=3)
        self.layers.setSelectionMode(qt_enum(QAbstractItemView, "SelectionMode", "SingleSelection"))
        self.layers.currentItemChanged.connect(self._update_buttons)
        layout.addWidget(self.layers, 1)

        buttons = QHBoxLayout()
        self.align_button = QPushButton(tr("Open in ALIGN"))
        self.align_button.clicked.connect(self._open_in_align)
        buttons.addWidget(self.align_button)
        buttons.addStretch(1)
        self.read_button = QPushButton(tr("Open read-only"))
        self.read_button.clicked.connect(lambda _checked=False: self._open(edit=False))
        buttons.addWidget(self.read_button)
        self.edit_button = QPushButton(tr("Edit in QGIS"))
        self.edit_button.clicked.connect(lambda _checked=False: self._open(edit=True))
        buttons.addWidget(self.edit_button)
        layout.addLayout(buttons)

        self._show_sheet(None)

    # -- data ---------------------------------------------------------------

    def refresh(self):
        """Fill the organizations from the Publish tab's read, then the services.

        An organization the plugin may not act in, or whose plan has no
        feature services, stays listed, greyed, its reason in the tooltip
        (TCK-2348, CA-17).
        """
        current = self.org_box.currentData()
        self.org_box.blockSignals(True)
        org_choice.fill(self.org_box, self._organizations() or [], lambda org: org["name"], org_choice.services_refusal, keep=current)
        self.org_box.blockSignals(False)
        self._on_org_changed()

    def forget(self):
        """Signed out: drop the organizations and services read for the account (CA-20)."""
        self.org_box.blockSignals(True)
        self.org_box.clear()
        self.org_box.blockSignals(False)
        self._on_org_changed()

    def choose_organization(self, org_id):
        """The Publish tab chose ``org_id`` (CA-24): follow it when it may be used here."""
        if org_id != self.org_box.currentData() and org_choice.choose(self.org_box, org_id) and self.isVisible():
            self._on_org_changed()

    def _on_org_changed(self, _index=None):
        self._services = []
        self.note.setText("")
        org_id = self.org_box.currentData()
        if org_id is None:
            self._fill_list()
            if not self.session.is_signed_in():
                self.note.setText(tr("Sign in to ALIGN to see your organization's feature services."))
            elif self.org_box.count() > 0:
                self.note.setText(tr("None of your organizations offers feature services to the ALIGN Connector: point at one to see why."))
            return
        if self._on_org_chosen is not None:
            self._on_org_chosen(org_id)
        QApplication.setOverrideCursor(qt_enum(Qt, "CursorShape", "WaitCursor"))
        try:
            listing = list_services(self.session, org_id)
            self._services = listing["services"]
            if listing["truncated"]:
                self.note.setText(tr("Only the first services are listed: search by name to find the others."))
            elif not self._services:
                self.note.setText(tr("No feature service you can read in this organization yet."))
        except ApiError as exc:
            self.note.setText(exc.message)
        finally:
            QApplication.restoreOverrideCursor()
        self._fill_list()

    def _fill_list(self, *_args):
        # The service in hand stays selected through a refresh (TCK-2348, CA-14).
        kept = self.list.currentItem().data(0, _role()) if self.list.currentItem() is not None else None
        self.list.blockSignals(True)
        self.list.clear()
        for service in services_model.matching(self._services, self.search.text()):
            item = QTreeWidgetItem([service.get("title") or service["serviceId"], services_model.access_label(service)])
            item.setData(0, _role(), service["serviceId"])
            item.setToolTip(0, service.get("description") or service.get("title") or "")
            self.list.addTopLevelItem(item)
        self.list.blockSignals(False)
        items = [self.list.topLevelItem(i) for i in range(self.list.topLevelItemCount())]
        same = next((item for item in items if kept is not None and item.data(0, _role()) == kept), None)
        self.list.setCurrentItem(same or self.list.topLevelItem(0))
        self._on_service_changed(self.list.currentItem(), None)

    def _selected_service(self):
        item = self.list.currentItem()
        service_id = item.data(0, _role()) if item is not None else None
        return next((s for s in self._services if s.get("serviceId") == service_id), None)

    def _on_service_changed(self, _current, _previous):
        service = self._selected_service()
        self._description = None
        if service is None:
            self._show_sheet(None)
            return
        QApplication.setOverrideCursor(qt_enum(Qt, "CursorShape", "WaitCursor"))
        try:
            self._description = describe_service(self.session, self.org_box.currentData(), service["serviceId"])
        except ApiError as exc:
            self.note.setText(exc.message)
        finally:
            QApplication.restoreOverrideCursor()
        self._show_sheet(service)

    def _show_sheet(self, service):
        while self.sheet.rowCount():
            self.sheet.removeRow(0)
        self.layers.clear()
        if service is None or self._description is None:
            self.sheet_box.setVisible(False)
            self._update_buttons()
            return
        self.sheet_box.setVisible(True)
        for label, value in services_model.sheet(service, self._description):
            text = QLabel(value)
            text.setWordWrap(True)
            text.setTextFormat(qt_enum(Qt, "TextFormat", "PlainText"))
            self.sheet.addRow(label, text)
        for row in services_model.layer_rows(self._description):
            item = QTreeWidgetItem([row["title"], row["geometry"], row["count"], row["fields"]])
            item.setData(0, _role(), row["layerId"])
            item.setToolTip(3, row["fields"])
            self.layers.addTopLevelItem(item)
        self.layers.setCurrentItem(self.layers.topLevelItem(0))
        self._update_buttons()

    # -- actions ------------------------------------------------------------

    def _selected_row(self):
        item = self.layers.currentItem()
        layer_id = item.data(0, _role()) if item is not None else None
        rows = services_model.layer_rows(self._description or {})
        return next((row for row in rows if row["layerId"] == layer_id), None)

    def _update_buttons(self, *_args):
        has_layer = self._selected_row() is not None
        self.read_button.setEnabled(has_layer)
        can_edit = has_layer and services_model.may_edit(self._description or {})
        self.edit_button.setEnabled(can_edit)
        self.edit_button.setToolTip("" if can_edit or not has_layer else tr("You may not edit this service."))
        self.align_button.setEnabled(self._selected_service() is not None)

    def _open(self, edit):
        service, row = self._selected_service(), self._selected_row()
        if service is not None and row is not None:
            self._on_open(self.org_box.currentData(), service, row, edit)

    def _open_in_align(self):
        service = self._selected_service()
        if service is not None:
            QDesktopServices.openUrl(QUrl(config.item_url(self.org_box.currentData(), service["serviceId"])))


def _role():
    return qt_enum(Qt, "ItemDataRole", "UserRole")


def _fit(tree, stretch):
    """One column takes the room left; the others fit their content, never cut."""
    header = tree.header()
    header.setStretchLastSection(False)
    for column in range(tree.columnCount()):
        mode = "Stretch" if column == stretch else "ResizeToContents"
        header.setSectionResizeMode(column, qt_enum(QHeaderView, "ResizeMode", mode))
