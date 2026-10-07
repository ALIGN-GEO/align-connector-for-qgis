# -*- coding: utf-8 -*-
"""The one dialog: what to publish, and where.

Deliberately small. A publish needs four answers — which layer, which
organization, under what name, and whether to send only the selection — and
anything else on this screen would be a decision the user did not ask to make.

Vector and raster layers sit in the same list, because from here they are the
same act: pick a layer, name it, send it. What differs happens afterwards and
on the server — a raster is normalised to a Cloud-Optimized GeoTIFF once it
lands — so it is not a choice to put in front of anyone.

One rule shapes it: **organizations are listed as ALIGN returns them, none
hidden.** It would be tempting to filter out the ones where the user is a
viewer and cannot publish. That would replace a clear refusal ("your role in
this organization does not allow publishing") with a silent absence, and leave
someone wondering why their organization is missing. The server decides; the
dialog shows.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import os

from qgis.core import Qgis, QgsMessageLog, QgsProject
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
)

from .. import api
from .exporter import publishable_kind, reprojection_notice
from . import raster
from . import symbology


class PublishDialog(QDialog):
    """Collects the four answers a publish needs."""

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setWindowTitle("Publish to ALIGN")
        self.setMinimumWidth(460)

        self._layers = _publishable_layers()
        self._orgs = []
        self.load_error = None
        #: The selected vector layer's symbology, converted (TCK-2201); None
        #: for a raster or a layer that takes ALIGN's default style.
        self._style = None

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.layer_box = QComboBox()
        for layer in self._layers:
            self.layer_box.addItem(layer.name(), layer.id())
        form.addRow("Layer", self.layer_box)

        self.org_box = QComboBox()
        form.addRow("Organization", self.org_box)

        # What the organization can still spend here, read from ALIGN when an
        # organization is picked (TCK-2179). Said before the transfer, so a
        # refusal for space or credits is never the first the user hears of
        # either. Blank when ALIGN could not say: the server guards regardless.
        self.capacity = QLabel()
        self.capacity.setWordWrap(True)
        self.capacity.setTextFormat(Qt.PlainText)
        self._capacity_line = None
        form.addRow("", self.capacity)

        self.name_edit = QLineEdit()
        form.addRow("Title", self.name_edit)

        self.description_edit = QPlainTextEdit()
        self.description_edit.setPlaceholderText(
            "What this layer holds, where it comes from, when it was collected — what a "
            "colleague would need to know before using it."
        )
        self.description_edit.setFixedHeight(72)
        form.addRow("Description", self.description_edit)

        # Only the two visibilities that make sense from a desktop. `public`
        # exposes data outside the organization and belongs on the web screen
        # that explains what that means; `group` would need an audience picker.
        self.visibility_box = QComboBox()
        self.visibility_box.addItem("Private — only you", "private")
        self.visibility_box.addItem("Organization — everyone in this organization", "org")
        form.addRow("Visibility", self.visibility_box)

        self.selection_box = QCheckBox("Publish only the selected features")
        form.addRow("", self.selection_box)

        layout.addLayout(form)

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setTextFormat(Qt.PlainText)
        layout.addWidget(self.hint)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        self.buttons.button(QDialogButtonBox.Ok).setText("Publish")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.layer_box.currentIndexChanged.connect(self._on_layer_changed)
        self.org_box.currentIndexChanged.connect(self._on_org_changed)
        self._on_layer_changed()

    # -- data ---------------------------------------------------------------

    def load_organizations(self):
        """Fetch the organizations. Call before showing the dialog.

        :returns: True when the dialog is usable; False when it is not, with
            :attr:`load_error` explaining why.
        """
        if not self._layers:
            self.load_error = "This project has no vector or raster layer to publish."
            return False

        try:
            self._orgs = api.list_organizations(self.session)
        except api.ApiError as exc:
            self.load_error = exc.message
            return False

        if not self._orgs:
            self.load_error = "You do not belong to any ALIGN organization yet."
            return False

        for org in self._orgs:
            label = org["name"] if not org.get("role") else "{}  ({})".format(org["name"], org["role"])
            self.org_box.addItem(label, org["id"])
        return True

    # -- interaction --------------------------------------------------------

    def _on_org_changed(self):
        """Refresh the capacity line for the organization now selected.

        One round trip, under the wait cursor. A failure leaves the line blank
        and a note in the QGIS log, never an error in the user's face: a
        capacity ALIGN cannot report does not stop a publish, it only removes a
        convenience, and the server guards either way.
        """
        self._capacity_line = None
        self.capacity.setText("")
        org = self.selected_org()
        if org is None:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            info = api.capacity(self.session, org["id"])
        except api.ApiError as exc:
            QgsMessageLog.logMessage(
                "Could not read the capacity of {}: {}".format(org["name"], exc.message),
                "ALIGN Connector",
                Qgis.Warning,
            )
            return
        finally:
            QApplication.restoreOverrideCursor()

        self._capacity_line = describe_capacity(info)
        self.capacity.setText(self._capacity_line)

    def _on_layer_changed(self):
        layer = self.selected_layer()
        if layer is None:
            return
        if not self.name_edit.text().strip():
            self.name_edit.setText(layer.name())

        self._style = None
        if publishable_kind(layer) == "raster":
            self._describe_raster(layer)
        else:
            self._describe_vector(layer)

    def _describe_vector(self, layer):
        self.selection_box.setVisible(True)
        selected = layer.selectedFeatureCount()
        self.selection_box.setEnabled(selected > 0)
        if selected > 0:
            self.selection_box.setText("Publish only the {} selected features".format(selected))
        else:
            self.selection_box.setChecked(False)
            self.selection_box.setText("Publish only the selected features (none selected)")

        total = layer.featureCount()
        text = (
            "{} features will be written and uploaded. Large layers keep uploading in the "
            "background — you can carry on working, and an interrupted transfer resumes.".format(
                selected if self.selection_box.isChecked() else total
            )
        )
        # A reprojection is announced before the send, never discovered after
        # (TCK-2199); a layer without a system is told what to fix.
        notice = reprojection_notice(layer.crs().authid(), layer.crs().isValid())
        if notice:
            text += " " + notice
        # The symbology travels too (TCK-2201), and what will not make it is
        # said here, before the send — never found on the map afterwards.
        conversion = symbology.convert(layer)
        self._style = conversion.style
        text += " " + describe_style(conversion)
        self.hint.setText(text)

    def _describe_raster(self, layer):
        """What a raster publish will actually do, said before it happens.

        A selection means nothing for a raster, so the checkbox goes away
        rather than sitting there greyed out. And the two facts worth knowing
        are how much will travel, and that the file is normalised on arrival —
        both are said now, not discovered in the Datastore afterwards.
        """
        self.selection_box.setChecked(False)
        self.selection_box.setVisible(False)

        source = raster.source_file_of(layer)
        if source:
            size = _readable_size(_size_of(source))
            self.hint.setText(
                "This layer's GeoTIFF will be sent as it is{}. ALIGN converts it to a "
                "Cloud-Optimized GeoTIFF on arrival; if that conversion has to reproject the "
                "data, your original is kept alongside it and both count against your "
                "storage.".format(" ({})".format(size) if size else "")
            )
            return

        self.hint.setText(
            "A compressed GeoTIFF will be written from this layer and sent. ALIGN converts it "
            "to a Cloud-Optimized GeoTIFF on arrival; if that conversion has to reproject the "
            "data, the file you sent is kept alongside it and both count against your storage."
        )

    # -- results ------------------------------------------------------------

    def selected_layer(self):
        layer_id = self.layer_box.currentData()
        return QgsProject.instance().mapLayer(layer_id) if layer_id else None

    def selected_org(self):
        org_id = self.org_box.currentData()
        return next((o for o in self._orgs if o["id"] == org_id), None)

    def style(self):
        """The selected layer's converted symbology to send, or None (TCK-2201)."""
        return self._style

    def capacity_line(self):
        """The capacity sentence shown for the selected organization, or None.

        Handed to the publish so a refusal about space or credits can say what
        the organization had, next to what the server says it lacked.
        """
        return self._capacity_line

    def description(self):
        """The description, or None. Empty means "no description", not "clear it"."""
        text = self.description_edit.toPlainText().strip()
        return text or None

    def visibility(self):
        return self.visibility_box.currentData()

    def asset_name(self):
        layer = self.selected_layer()
        return self.name_edit.text().strip() or (layer.name() if layer else "Layer")

    def only_selection(self):
        return self.selection_box.isChecked() and self.selection_box.isEnabled()


def _publishable_layers():
    """The project's layers this plugin can send, in project order.

    The test is `publishable_kind`, shared with the export — so the list can
    never offer something the publish would then refuse.
    """
    return [
        layer
        for layer in QgsProject.instance().mapLayers().values()
        if publishable_kind(layer) is not None
    ]


def describe_style(conversion):
    """What the publisher reads about the style before sending (TCK-2201).

    ``Style: categorized on zone, 4 classes. Not transmitted: hatched fill
    rendered flat; labels.`` A layer that takes ALIGN's default style says so.
    """
    if conversion.style is None:
        head = "Style: ALIGN's default will apply."
    else:
        head = "Style: {}.".format(conversion.summary or "transmitted")
    if conversion.report:
        head += " Simplified: " + " ".join(conversion.report)
    return head


def describe_capacity(capacity_info):
    """One line a publisher reads under the organization picker.

    ``Storage 1.2 GB of 10.0 GB used · 844 credits``. An unlimited storage says
    so; a figure ALIGN did not give is left out rather than guessed; nothing at
    all gives an empty string, and the label simply stays blank.
    """
    parts = []
    storage = capacity_info.get("storage") or {}
    used = storage.get("usedBytes")
    limit = storage.get("limitBytes")
    if used is not None:
        used_text = _readable_size(used) or "0 bytes"
        if limit:
            parts.append("Storage {} of {} used".format(used_text, _readable_size(limit)))
        else:
            parts.append("Storage {} used, no limit".format(used_text))
    available = (capacity_info.get("credits") or {}).get("available")
    if available is not None:
        parts.append("{} credit{}".format(available, "" if available == 1 else "s"))
    return " · ".join(parts)


def _size_of(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def _readable_size(size_bytes):
    """A size a person reads at a glance, or None when it is unknown."""
    if not size_bytes:
        return None
    units = ("bytes", "KB", "MB", "GB", "TB")
    value = float(size_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return "{:.0f} {}".format(value, unit) if unit == "bytes" else "{:.1f} {}".format(value, unit)
        value /= 1024.0
    return None
