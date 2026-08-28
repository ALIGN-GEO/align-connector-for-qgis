# -*- coding: utf-8 -*-
"""The one dialog: what to publish, and where.

Deliberately small. A publish needs four answers — which layer, which
organization, under what name, and whether to send only the selection — and
anything else on this screen would be a decision the user did not ask to make.

One rule shapes it: **organizations are listed as ALIGN returns them, none
hidden.** It would be tempting to filter out the ones where the user is a
viewer and cannot publish. That would replace a clear refusal ("your role in
this organization does not allow publishing") with a silent absence, and leave
someone wondering why their organization is missing. The server decides; the
dialog shows.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from qgis.core import QgsMapLayerType, QgsProject
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
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

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.layer_box = QComboBox()
        for layer in self._layers:
            self.layer_box.addItem(layer.name(), layer.id())
        form.addRow("Layer", self.layer_box)

        self.org_box = QComboBox()
        form.addRow("Organization", self.org_box)

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
        self._on_layer_changed()

    # -- data ---------------------------------------------------------------

    def load_organizations(self):
        """Fetch the organizations. Call before showing the dialog.

        :returns: True when the dialog is usable; False when it is not, with
            :attr:`load_error` explaining why.
        """
        if not self._layers:
            self.load_error = "This project has no vector layer to publish."
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

    def _on_layer_changed(self):
        layer = self.selected_layer()
        if layer is None:
            return
        if not self.name_edit.text().strip():
            self.name_edit.setText(layer.name())

        selected = layer.selectedFeatureCount()
        self.selection_box.setEnabled(selected > 0)
        if selected > 0:
            self.selection_box.setText("Publish only the {} selected features".format(selected))
        else:
            self.selection_box.setChecked(False)
            self.selection_box.setText("Publish only the selected features (none selected)")

        total = layer.featureCount()
        self.hint.setText(
            "{} features will be written and uploaded. Large layers keep uploading in the "
            "background — you can carry on working, and an interrupted transfer resumes.".format(
                selected if self.selection_box.isChecked() else total
            )
        )

    # -- results ------------------------------------------------------------

    def selected_layer(self):
        layer_id = self.layer_box.currentData()
        return QgsProject.instance().mapLayer(layer_id) if layer_id else None

    def selected_org(self):
        org_id = self.org_box.currentData()
        return next((o for o in self._orgs if o["id"] == org_id), None)

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
    """Vector layers of the current project, in project order."""
    layers = []
    for layer in QgsProject.instance().mapLayers().values():
        try:
            is_vector = layer.type() == QgsMapLayerType.VectorLayer
        except AttributeError:  # pragma: no cover — enum moved namespace in Qt6 builds
            is_vector = layer.type() == 0
        if is_vector and layer.isValid():
            layers.append(layer)
    return layers
