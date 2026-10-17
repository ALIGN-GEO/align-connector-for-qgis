# -*- coding: utf-8 -*-
"""The Publish view: which layers, to which organization, under what names.

What the modal dialog asked for one layer, this asks for any number of them:
one, several, or the whole project. Each layer keeps its own title and its
own "selection only" choice; the organization, the visibility and the
description are the batch's, and so is the web map that can assemble the
layers once they have all landed (TCK-2204). The briefing of the highlighted
layer (what will travel, what the style and the metadata become, what is
simplified) is shown before the send, never discovered after; a layer that
cannot go is listed greyed with its reason, never hidden.

Organizations are listed as ALIGN returns them, none hidden: the server
decides who may publish where, and its refusal is what the user is shown.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from qgis.core import Qgis, QgsMessageLog, QgsProject
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import api
from ..i18n import tr
from ..publish import briefing, memory, support, tiles
from ..publish.wording import describe_capacity
from ..qtcompat import qt_enum
from . import batch


class PublishView(QWidget):
    """Collects what a batch needs, and hands it to the caller in one piece."""

    def __init__(self, session, on_publish, on_rows=None, on_show_messages=None, parent=None):
        """
        :param on_publish: called with one :class:`batch.Batch` per click on Publish
        :param on_rows: called with the rows each time the list is rebuilt (the Messages view listens)
        :param on_show_messages: called when the user asks to see the refusals
        """
        super().__init__(parent)
        self.session = session
        self._on_publish = on_publish
        self._on_rows = on_rows
        self._on_show_messages = on_show_messages
        self._orgs = []
        #: Whether ALIGN was asked at least once: the diagnostic tells "none"
        #: from "never loaded" (TCK-2219).
        self._orgs_loaded = False
        self._capacity_line = None
        #: Whether the organization selected may publish feature services
        #: (TCK-2320), as its capacity answer says; the publication checks again.
        self._feature_services = False
        self._titles = {}
        self._selection_only = set()
        #: Layers the publisher wants as NEW items although they remember one (TCK-2218).
        self._publish_as_new = set()
        self._rows = []
        self._attached = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # -- the layers ------------------------------------------------------
        self.all_box = QCheckBox(tr("Whole project"))
        self.all_box.toggled.connect(self._on_all_toggled)
        layout.addWidget(self.all_box)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("Layer"), tr("Kind")])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(qt_enum(QAbstractItemView, "SelectionMode", "SingleSelection"))
        self.tree.header().setSectionResizeMode(0, qt_enum(QHeaderView, "ResizeMode", "Stretch"))
        self.tree.header().setStretchLastSection(False)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.currentItemChanged.connect(self._on_current_changed)
        layout.addWidget(self.tree, 1)

        # What cannot go: one line here, the reasons in the Messages view (TCK-2210).
        self.blocked = QPushButton()
        self.blocked.setFlat(True)
        self.blocked.setSizePolicy(qt_enum(QSizePolicy, "Policy", "Preferred"), qt_enum(QSizePolicy, "Policy", "Fixed"))
        self.blocked.clicked.connect(lambda _checked=False: self._on_show_messages() if self._on_show_messages else None)
        self.blocked.setVisible(False)
        layout.addWidget(self.blocked)

        # -- the highlighted layer -------------------------------------------
        form = QFormLayout()
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText(tr("Title on ALIGN"))
        self.title_edit.textEdited.connect(self._on_title_edited)
        form.addRow(tr("Title"), self.title_edit)
        self.selection_box = QCheckBox(tr("Publish only the selected features"))
        self.selection_box.toggled.connect(self._on_selection_toggled)
        form.addRow("", self.selection_box)
        # A layer that remembers its item offers to replace it (TCK-2218);
        # unticked, it goes as a new item. Hidden for a layer never published.
        self.replace_box = QCheckBox(tr("Replace the item already on ALIGN"))
        self.replace_box.toggled.connect(self._on_replace_toggled)
        self.replace_box.setVisible(False)
        form.addRow("", self.replace_box)
        # The briefing can run to several sentences: a bounded, scrollable
        # box keeps the form below it in reach.
        self.briefing = QPlainTextEdit()
        self.briefing.setReadOnly(True)
        self.briefing.setFixedHeight(88)
        self.briefing.setFrameShape(qt_enum(QFrame, "Shape", "NoFrame"))
        form.addRow("", self.briefing)
        layout.addLayout(form)

        # -- the batch -------------------------------------------------------
        batch_form = QFormLayout()
        self.org_box = QComboBox()
        self.org_box.currentIndexChanged.connect(self._on_org_changed)
        batch_form.addRow(tr("Organization"), self.org_box)
        self.capacity = QLabel()
        self.capacity.setWordWrap(True)
        self.capacity.setTextFormat(qt_enum(Qt, "TextFormat", "PlainText"))
        batch_form.addRow("", self.capacity)
        # Only the two visibilities that make sense from a desktop. `public`
        # exposes data outside the organization and belongs on the web screen
        # that explains what that means; `group` would need an audience picker.
        self.visibility_box = QComboBox()
        self.visibility_box.addItem(tr("Private: only you"), "private")
        self.visibility_box.addItem(tr("Organization: everyone in this organization"), "org")
        batch_form.addRow(tr("Visibility"), self.visibility_box)
        self.description_edit = QPlainTextEdit()
        self.description_edit.setPlaceholderText(
            tr(
                "What these layers hold, where they come from, when they were collected: what a "
                "colleague would need to know before using them."
            )
        )
        self.description_edit.setFixedHeight(64)
        batch_form.addRow(tr("Description"), self.description_edit)

        # -- as feature services (TCK-2320) -----------------------------------
        # Offered only where ALIGN would accept it: shown for an organization
        # whose plan includes feature services, hidden otherwise.
        self.service_box = QCheckBox(tr("Publish vector layers as editable feature services"))
        self.service_box.setToolTip(
            tr(
                "ALIGN moves the features into your organization's database, where QGIS and the Map builder "
                "edit them live. Each layer becomes a new feature service."
            )
        )
        self.service_box.toggled.connect(self._on_service_toggled)
        self.service_box.setVisible(False)
        batch_form.addRow("", self.service_box)

        # -- the web map (TCK-2204) -------------------------------------------
        self.webmap_box = QCheckBox(tr("Also create a web map with these layers"))
        self.webmap_box.toggled.connect(self._on_webmap_toggled)
        batch_form.addRow("", self.webmap_box)
        self.webmap_title = QLineEdit()
        self.webmap_title.setPlaceholderText(tr("Web map title"))
        self.webmap_title.setEnabled(False)
        batch_form.addRow(tr("Web map"), self.webmap_title)
        self.webmap_note = QLabel(tr("Layers panel order and visibility, groups flattened, opened on the canvas view."))
        self.webmap_note.setWordWrap(True)
        self.webmap_note.setTextFormat(qt_enum(Qt, "TextFormat", "PlainText"))
        self.webmap_note.setVisible(False)
        batch_form.addRow("", self.webmap_note)
        layout.addLayout(batch_form)

        self.publish_button = QPushButton(batch.describe_plan([]))
        self.publish_button.setEnabled(False)
        self.publish_button.clicked.connect(self._publish)
        layout.addWidget(self.publish_button)

    # -- lifecycle ----------------------------------------------------------

    def attach(self):
        """Follow the project: a layer added, removed or moved reshapes the list.

        Done on first use, not at construction: QGIS loads plugins while it is
        still starting, and nothing here should touch the project before the
        panel is actually opened.
        """
        if self._attached:
            return
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        root.addedChildren.connect(self._on_project_changed)
        root.removedChildren.connect(self._on_project_changed)
        project.cleared.connect(self._on_project_changed)
        self._attached = True

    def detach(self):
        """Stop following the project. Called when the plugin unloads."""
        if not self._attached:
            return
        project = QgsProject.instance()
        for signal in (project.layerTreeRoot().addedChildren, project.layerTreeRoot().removedChildren, project.cleared):
            try:
                signal.disconnect(self._on_project_changed)
            except (TypeError, RuntimeError):
                pass
        self._attached = False

    def showEvent(self, event):  # noqa: N802 — Qt's name
        super().showEvent(event)
        # The highlighted layer may have been restyled or documented since:
        # what is read here is what will be sent.
        self._on_current_changed(self.tree.currentItem(), None)

    # -- data ---------------------------------------------------------------

    def refresh(self):
        """Reload organizations and layers. Called when the panel opens."""
        self.attach()
        self.refresh_organizations()
        self.refresh_layers()

    def organizations(self):
        """The organizations as last loaded (name and role), or ``None`` when
        ALIGN was never asked: for the diagnostic, which reads, never calls."""
        if not self._orgs_loaded:
            return None
        return [{"name": org.get("name"), "role": org.get("role")} for org in self._orgs]

    def refresh_organizations(self):
        self.capacity.setText("")
        self.org_box.blockSignals(True)
        self.org_box.clear()
        self._orgs = []
        self._orgs_loaded = True
        try:
            self._orgs = api.list_organizations(self.session)
        except api.ApiError as exc:
            self.capacity.setText(exc.message)
        for org in self._orgs:
            label = org["name"] if not org.get("role") else "{}  ({})".format(org["name"], _role_label(org["role"]))
            self.org_box.addItem(label, org["id"])
        self.org_box.blockSignals(False)
        if not self._orgs and not self.capacity.text():
            self.capacity.setText(tr("You do not belong to any ALIGN organization yet."))
        self._on_org_changed()

    def refresh_layers(self):
        """Rebuild the list from the project, in the Layers panel's order,
        keeping what was checked."""
        kept = set(self.checked_ids())
        current = self._current_layer_id()
        self._rows = batch.rows_of(_project_layers())
        self.tree.blockSignals(True)
        self.tree.clear()
        for row in self._rows:
            item = QTreeWidgetItem([row.name, _kind_label(row.kind)])
            item.setData(0, _USER_ROLE(), row.layer_id)
            if row.publishable:
                item.setFlags(item.flags() | qt_enum(Qt, "ItemFlag", "ItemIsUserCheckable"))
                item.setCheckState(0, _state(row.layer_id in kept))
                item.setToolTip(0, row.name)
            else:
                item.setFlags(item.flags() & ~qt_enum(Qt, "ItemFlag", "ItemIsEnabled"))
                item.setText(1, row.reason)
                item.setToolTip(0, row.reason)
                item.setToolTip(1, row.reason)
            self.tree.addTopLevelItem(item)
        self.tree.blockSignals(False)
        count = sum(1 for row in self._rows if not row.publishable)
        self.blocked.setText(
            (tr("1 layer cannot be published. See Messages.") if count == 1 else tr("{} layers cannot be published. See Messages.").format(count))
        )
        self.blocked.setVisible(count > 0)
        if self._on_rows:
            self._on_rows(self._rows)
        if not self.webmap_title.text().strip():
            self.webmap_title.setText(_project_title())
        self._sync_all_box()
        self._update_button()
        self.tree.setCurrentItem(self._item_of(current) or self._first_enabled_item())

    def check_only(self, layer_id):
        """Check one layer and nothing else: the layer context menu's entry point."""
        for item in self._items():
            if not _checkable(item):
                continue
            item.setCheckState(0, _state(item.data(0, _USER_ROLE()) == layer_id))
        self.tree.setCurrentItem(self._item_of(layer_id))

    def checked_ids(self):
        return [item.data(0, _USER_ROLE()) for item in self._items() if item.checkState(0) == _state(True)]

    def selected_org_id(self):
        org = self.selected_org()
        return org["id"] if org is not None else None

    def selected_org(self):
        org_id = self.org_box.currentData()
        return next((o for o in self._orgs if o["id"] == org_id), None)

    # -- interaction --------------------------------------------------------

    def _on_project_changed(self, *_args):
        self.refresh_layers()

    def _on_all_toggled(self, checked):
        self.tree.blockSignals(True)
        for item in self._items():
            if _checkable(item):
                item.setCheckState(0, _state(checked))
        self.tree.blockSignals(False)
        self._update_button()

    def _on_item_changed(self, _item, _column):
        self._sync_all_box()
        self._update_button()

    def _service_mode(self):
        return self._feature_services and self.service_box.isChecked()

    def _on_service_toggled(self, _checked):
        # A web map is assembled from files, and a feature service is not one
        # yet: the two options do not go together in one click.
        service = self._service_mode()
        if service:
            self.webmap_box.setChecked(False)
        self.webmap_box.setEnabled(not service)
        self._on_current_changed(self.tree.currentItem(), None)
        self._update_button()

    def _on_webmap_toggled(self, checked):
        self.webmap_title.setEnabled(checked)
        self.webmap_note.setVisible(checked)
        if checked and not self.webmap_title.text().strip():
            self.webmap_title.setText(_project_title())

    def _on_current_changed(self, current, _previous):
        layer = QgsProject.instance().mapLayer(current.data(0, _USER_ROLE())) if current is not None else None
        if layer is None:
            self.briefing.setPlainText("")
            self.title_edit.setText("")
            self.selection_box.setVisible(False)
            self.replace_box.setVisible(False)
            return
        layer_id = layer.id()
        row = next((r for r in self._rows if r.layer_id == layer_id), None)
        info = briefing.brief(layer, layer_id in self._selection_only)

        # Already on ALIGN (TCK-2218): say where, and offer to replace it when
        # the batch goes to that organization; elsewhere it goes as new.
        link = batch.replaceable(row, self.selected_org_id()) if row is not None else None
        if self._service_mode() and info.kind == "vector":
            # A feature service is always a new item.
            link = None
        self.replace_box.blockSignals(True)
        self.replace_box.setVisible(link is not None)
        self.replace_box.setChecked(link is not None and layer_id not in self._publish_as_new)
        self.replace_box.blockSignals(False)

        self.title_edit.blockSignals(True)
        self.title_edit.setText(self._titles.get(layer_id) or (row.title if row else info.title) or "")
        self.title_edit.blockSignals(False)

        self.selection_box.blockSignals(True)
        selected = layer.selectedFeatureCount() if info.kind == "vector" else 0
        self.selection_box.setVisible(info.kind == "vector")
        self.selection_box.setEnabled(selected > 0)
        self.selection_box.setChecked(layer_id in self._selection_only and selected > 0)
        self.selection_box.setText(
            tr("Publish only the {} selected features").format(selected)
            if selected > 0
            else tr("Publish only the selected features (none selected)")
        )
        self.selection_box.blockSignals(False)
        text = info.text
        if row is not None and row.link is not None:
            text = memory.describe(row.link) + ("\n" + text if text else "")
        self.briefing.setPlainText(text)

    def _on_title_edited(self, text):
        current = self.tree.currentItem()
        if current is not None:
            self._titles[current.data(0, _USER_ROLE())] = text

    def _on_replace_toggled(self, checked):
        current = self.tree.currentItem()
        if current is None:
            return
        layer_id = current.data(0, _USER_ROLE())
        if checked:
            self._publish_as_new.discard(layer_id)
        else:
            self._publish_as_new.add(layer_id)
        self._update_button()

    def _on_selection_toggled(self, checked):
        current = self.tree.currentItem()
        if current is None:
            return
        layer_id = current.data(0, _USER_ROLE())
        if checked:
            self._selection_only.add(layer_id)
        else:
            self._selection_only.discard(layer_id)
        self._on_current_changed(current, None)

    def _on_org_changed(self, _index=None):
        """Refresh the capacity line for the organization now selected (TCK-2179).

        A failure leaves the line blank and a note in the QGIS log, never an
        error in the user's face: a capacity ALIGN cannot report does not stop
        a publish, it only removes a convenience, and the server guards.
        """
        self._capacity_line = None
        self._set_feature_services(False)
        org = self.selected_org()
        self._update_button()
        # Whether the highlighted layer replaces its item depends on the
        # organization now selected (TCK-2218): the form follows the switch.
        self._on_current_changed(self.tree.currentItem(), None)
        if org is None:
            return
        self.capacity.setText("")
        QApplication.setOverrideCursor(qt_enum(Qt, "CursorShape", "WaitCursor"))
        try:
            info = api.capacity(self.session, org["id"])
        except api.ApiError as exc:
            QgsMessageLog.logMessage(
                tr("Could not read the capacity of {}: {}").format(org["name"], exc.message),
                "ALIGN Connector",
                qt_enum(Qgis, "MessageLevel", "Warning"),
            )
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._capacity_line = describe_capacity(info)
        self.capacity.setText(self._capacity_line)
        self._set_feature_services(info.get("featureServices") is True)

    def _set_feature_services(self, offered):
        # A session signed in before the plugin asked for the features scopes
        # would upload the file, then be refused the service: say it here.
        granted = offered and self.session.has_scope("features:write")
        self._feature_services = granted
        self.service_box.setVisible(offered)
        self.service_box.setEnabled(granted)
        if offered and not granted:
            self.service_box.setChecked(False)
        self.service_box.setText(
            tr("Publish vector layers as editable feature services")
            if granted or not offered
            else tr("Publish vector layers as editable feature services (sign in to ALIGN again to use it)")
        )
        self._on_service_toggled(self.service_box.isChecked())

    def _sync_all_box(self):
        checkable = [item for item in self._items() if _checkable(item)]
        self.all_box.blockSignals(True)
        self.all_box.setChecked(bool(checkable) and all(item.checkState(0) == _state(True) for item in checkable))
        self.all_box.blockSignals(False)

    def _update_button(self):
        plans = self._plans()
        self.publish_button.setText(batch.describe_plan(plans))
        self.publish_button.setEnabled(bool(plans) and self.selected_org() is not None)

    def _plans(self):
        return batch.plan(
            self._rows,
            self.checked_ids(),
            self._titles,
            self._selection_only,
            org_id=self.selected_org_id(),
            publish_as_new=self._publish_as_new,
            as_service=self._service_mode(),
        )

    def _publish(self):
        org = self.selected_org()
        if org is None:
            return
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        requests = []
        for item in self._plans():
            layer = project.mapLayer(item.layer_id)
            if layer is None:
                continue
            # The briefing is read here, on the interface thread, once per
            # layer: the converted style and the metadata go with the task.
            # So do what the web map will need: whether QGIS shows the layer
            # (its group counted), and its geometry.
            node = root.findLayer(item.layer_id)
            visible = node.isVisible() if node is not None else True
            verdict = support.verdict(layer)
            requests.append(
                batch.Request(
                    layer,
                    item,
                    briefing.brief(layer, item.selected_only),
                    visible=visible,
                    geometry_type=support.geojson_geometry_type(layer) if verdict.kind != "archive" else None,
                    source_layers=tiles.source_layers_of(layer) if verdict.kind == "archive" else None,
                    unplaceable=verdict.unplaceable,
                )
            )
        if not requests:
            return
        web_map_name = None
        if self.webmap_box.isChecked():
            web_map_name = self.webmap_title.text().strip() or _project_title()
        self._on_publish(
            batch.Batch(
                org,
                self.visibility_box.currentData(),
                self.description_edit.toPlainText().strip() or None,
                self._capacity_line,
                requests,
                web_map_name=web_map_name,
            )
        )
        # What left is left: a second click must not send it again.
        self._on_all_toggled(False)
        self._sync_all_box()

    # -- items --------------------------------------------------------------

    def _items(self):
        return [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]

    def _item_of(self, layer_id):
        return next((item for item in self._items() if item.data(0, _USER_ROLE()) == layer_id), None)

    def _first_enabled_item(self):
        return next((item for item in self._items() if _checkable(item)), None)

    def _current_layer_id(self):
        current = self.tree.currentItem()
        return current.data(0, _USER_ROLE()) if current is not None else None


def _kind_label(kind):
    """The Type column, in the user's language; a dash when the verdict has no kind."""
    labels = {"vector": tr("vector"), "raster": tr("raster"), "archive": tr("archive"), "reference": tr("reference")}
    return labels.get(kind, "—")


def _role_label(role):
    """An organization role as ALIGN names it (a member is an editor), translated."""
    labels = {"owner": tr("owner"), "admin": tr("admin"), "member": tr("editor"), "viewer": tr("viewer")}
    return labels.get(role, role)


def _project_layers():
    """The project's layers in the Layers panel's order; the map's order as a
    fallback for a project whose tree is not there to ask."""
    project = QgsProject.instance()
    try:
        layers = [node.layer() for node in project.layerTreeRoot().findLayers()]
        layers = [layer for layer in layers if layer is not None]
    except (AttributeError, RuntimeError):
        layers = []
    return layers or list(project.mapLayers().values())


def _project_title():
    """The project's title, else its file name, else a name that says so."""
    project = QgsProject.instance()
    for name in (project.title(), project.baseName()):
        if name and name.strip():
            return name.strip()
    return tr("Untitled map")


def _USER_ROLE():  # noqa: N802 — reads as the Qt constant it stands for
    return qt_enum(Qt, "ItemDataRole", "UserRole")


def _state(checked):
    return qt_enum(Qt, "CheckState", "Checked" if checked else "Unchecked")


def _checkable(item):
    return bool(item.flags() & qt_enum(Qt, "ItemFlag", "ItemIsUserCheckable")) and bool(
        item.flags() & qt_enum(Qt, "ItemFlag", "ItemIsEnabled")
    )
