# -*- coding: utf-8 -*-
"""The feature-service layers in QGIS' interface (TCK-2348).

What this wires, and takes back on unload:

* the **signer**: the plugin signs its service layers' requests with its
  session (``auth/request_signer``), from QGIS 3.36 on; the token is kept
  fresh by a timer while the project holds a service layer;
* the **context menu** of a service layer and of its local original: "Edit in
  QGIS", or "Open read-only" when ALIGN last said the user may not edit, at any
  time (CA-6). ALIGN decides again on the click;
* a project reopened **without a session**: its service layers say "Sign in
  to ALIGN to read this layer", with the button, and read again after the
  sign-in (CA-11);
* the service's **input rules** (TCK-2343): set on the layer when it opens,
  and again when the project reopens or the session comes back, the service
  being the source; a save ALIGN refuses for its rules is explained.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import contextlib

from qgis.core import Qgis, QgsMapLayer, QgsProject
from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtWidgets import QAction, QPushButton

from . import config
from .api import ApiError
from .auth.request_signer import RequestSigner
from .i18n import tr
from .panel.services_model import may_edit
from .publish import service_form, service_layer, service_memory
from .qtcompat import qt_enum
from .services_api import describe_service
from .session_watch import SessionWatch

PLUGIN_NAME = config.PLUGIN_NAME
#: How often the token is checked while the project holds a service layer.
_FRESHNESS_MS = 60 * 1000


class ServiceLayerActions(object):
    def __init__(self, iface, session, message, icon=None):
        self.iface = iface
        self.session = session
        #: ``(text, level) -> None``: the plugin's message bar.
        self._message = message
        #: The plugin's icon, on its menu entries as on "Publish to ALIGN…" (TCK-2348, CA-22).
        self._icon = icon
        self._signer = None
        self._timer = None
        self._watch = None
        self._edit_action = None
        self._read_action = None
        self._attached = False

    # -- lifecycle ----------------------------------------------------------

    def attach(self):
        """Install the signer and the menu entries; nothing below QGIS 3.36."""
        if self._attached or not service_layer.editing_supported():
            return
        self._attached = True
        self.session.prime_signer()
        self._signer = RequestSigner(self.session.snapshot, config.api_url)
        self._signer.install()

        parent = self.iface.mainWindow()
        self._edit_action = QAction(tr("Edit in QGIS"), parent)
        self._edit_action.triggered.connect(lambda _checked=False: self._on_menu(edit=True))
        self._read_action = QAction(tr("Open read-only"), parent)
        self._read_action.triggered.connect(lambda _checked=False: self._on_menu(edit=False))
        for action in (self._edit_action, self._read_action):
            if self._icon is not None:
                action.setIcon(self._icon)
            # Not for all layers: only those added one by one below.
            self.iface.addCustomActionForLayerType(action, None, _vector_type(), False)

        project = QgsProject.instance()
        project.layersAdded.connect(self._on_layers_added)
        project.readProject.connect(self._on_project_read)
        self.session.signed_in.connect(self._on_signed_in)
        self._on_layers_added(list(project.mapLayers().values()))

        self._timer = QTimer()
        self._timer.setInterval(_FRESHNESS_MS)
        self._timer.timeout.connect(self._keep_fresh)
        self._timer.start()

        self._watch = SessionWatch(
            self.iface,
            self.session,
            service_layer.service_layers,
            on_confirmed=lambda: self._reload_all(),
            on_rules_refusal=self._explain_refusal,
        )
        self._watch.attach()

    def detach(self):
        """Take back everything :meth:`attach` added."""
        if not self._attached:
            return
        self._attached = False
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self._watch is not None:
            self._watch.detach()
            self._watch = None
        project = QgsProject.instance()
        for signal, slot in (
            (project.layersAdded, self._on_layers_added),
            (project.readProject, self._on_project_read),
            (self.session.signed_in, self._on_signed_in),
        ):
            # Never connected, or already gone with QGIS: nothing left to take back.
            with contextlib.suppress(TypeError, RuntimeError):
                signal.disconnect(slot)
        for action in (self._edit_action, self._read_action):
            if action is not None:
                self.iface.removeCustomActionForLayerType(action)
        self._edit_action = self._read_action = None
        if self._signer is not None:
            self._signer.uninstall()
            self._signer = None

    # -- the menu (CA-6) ----------------------------------------------------

    def _on_layers_added(self, layers):
        for layer in layers:
            self._offer(layer)

    def _offer(self, layer):
        ref = service_memory.read(layer)
        if ref is None or self._edit_action is None:
            return
        action = self._edit_action if ref.can_edit else self._read_action
        self.iface.addCustomActionForLayer(action, layer)

    def _on_menu(self, edit):
        layer = self.iface.layerTreeView().currentLayer() or self.iface.activeLayer()
        if layer is not None:
            self.edit_layer(layer) if edit else self.read_layer(layer)

    def edit_layer(self, layer):
        """Edit a service layer, or the service of a published original."""
        ref = service_memory.read(layer)
        if ref is None:
            return
        description = self._describe(ref)
        if description is None:
            return
        allowed = may_edit(description)
        if allowed != ref.can_edit:
            service_memory.set_can_edit(layer, allowed)
            self._readd(layer)
        if not allowed:
            self._message(tr("You may not edit this feature service: it opens for reading."), _level("Info"))
            self.read_layer(layer)
            return
        target = self._service_layer_of(layer, ref, read_only=False, description=description)
        if target is None:
            return
        if target is layer:
            # Already open: its rules as the service has them now (CA-5).
            self._set_rules(target, ref, description, announce=False)
        target.setReadOnly(False)
        service_memory.rename_for_mode(target, ref, read_only=False)
        target.startEditing()
        self.iface.setActiveLayer(target)
        self._message(tr("“{}” is in editing mode: save to write the changes to ALIGN.").format(target.name()), _level("Success"))

    def read_layer(self, layer):
        ref = service_memory.read(layer)
        if ref is None:
            return
        target = self._service_layer_of(layer, ref, read_only=True)
        if target is not None:
            self.iface.setActiveLayer(target)

    def _service_layer_of(self, layer, ref, read_only, description=None):
        """The service layer itself, the one already open for this original, or a new one in its place."""
        if ref.kind == service_memory.SERVICE:
            return layer
        for opened, opened_ref in service_layer.service_layers():
            if opened_ref.service_id == ref.service_id and opened_ref.layer_id == ref.layer_id:
                return opened
        return self.open(ref, read_only=read_only, local_layer_id=layer.id(), description=description)

    def open(self, ref, read_only=False, local_layer_id=None, description=None):
        """Open a service layer, with the service's input rules; ``None`` (and
        the reason in the bar) when it cannot be."""
        if not self.session.keep_signer_fresh():
            self._message(tr("Sign in to ALIGN to open this feature service."), _level("Warning"))
            return None
        try:
            layer = service_layer.open_service(ref, read_only=read_only, local_layer_id=local_layer_id)
        except service_layer.ServiceLayerUnavailable as exc:
            self._message(str(exc), _level("Warning"))
            return None
        self._set_rules(layer, ref, description)
        return layer

    def _describe(self, ref):
        try:
            return describe_service(self.session, ref.org_id, ref.service_id, counts=False)
        except ApiError as exc:
            self._message(exc.message, _level("Warning"))
            return None

    # -- the input rules (TCK-2343) -----------------------------------------

    def _set_rules(self, layer, ref, description=None, announce=True):
        """The service's input rules on the layer; what ALIGN alone checks is
        said once, when the layer opens (CA-3)."""
        described = service_form.layer_description(description or self._describe(ref), ref.layer_id)
        if described is None:
            return
        left = service_form.apply_form(layer, described)
        if left and announce:
            self._message(tr("ALIGN checks these rules of “{}” only when you save: {}.").format(layer.name(), _clause(tr("; ").join(left))), _level("Info"))

    def _refresh_rules(self, layers):
        """The rules again from ALIGN, one read per service (CA-5)."""
        described = {}
        for layer in layers:
            ref = service_memory.read(layer)
            if ref is None or not layer.isValid():
                continue
            key = (ref.org_id, ref.service_id)
            if key not in described:
                described[key] = self._describe(ref)
            if described[key] is not None:
                self._set_rules(layer, ref, described[key], announce=False)

    def _explain_refusal(self, layer, answer=None):
        """ALIGN refused a save for its rules (CA-6): say which, in ALIGN's
        words when they reached the plugin, else from the edits still waiting."""
        lines = service_form.explain_refusal(layer)
        reason = answer or service_form.align_answer(layer) or (tr("; ").join(lines) if lines else tr("a rule ALIGN checks when it saves refused a value"))
        self._message(
            tr("ALIGN refused to save “{}”: {}. The edits stay in the layer: correct them and save again.").format(layer.name(), _clause(reason)),
            _level("Warning"),
        )

    def _readd(self, layer):
        for action in (self._edit_action, self._read_action):
            if action is not None:
                self.iface.removeCustomActionForLayer(action, layer)
        self._offer(layer)

    # -- entry points of the plugin -----------------------------------------

    def offer_after_publish(self, task, text, level):
        """The service is ready (TCK-2320, ADR-0063 §3): offer to edit it from
        QGIS in place of the local layer. The local layer remembers its service,
        so its context menu offers the same at any time (CA-6); the bar's button
        is a shortcut. Below QGIS 3.36 the address is given instead."""
        ref = service_memory.ServiceRef(
            task.org_id, task.service_id, task.service_layer_id, task.asset_name, task.service_srid, service_memory.ORIGINAL
        )
        local = QgsProject.instance().mapLayer(task.layer_id)
        if local is not None:
            service_memory.remember(local, ref, service_memory.ORIGINAL)
            self._offer(local)
        if not self._attached:
            text += " " + tr("Editing it from QGIS needs QGIS 3.36 or later. Its OGC API Features address: {}").format(
                config.feature_service_ogc_url(task.org_id, task.service_id)
            )
            self._message(text, level)
            return
        bar = self.iface.messageBar()
        widget = bar.createMessage(PLUGIN_NAME, text)
        button = QPushButton(tr("Edit in QGIS"))
        button.setToolTip(tr("Opens the feature service in place of the local layer, which is kept, hidden, in the group “Local originals”."))
        button.clicked.connect(lambda _checked=False, layer=local, w=widget: self._edit_published(layer, w))
        widget.layout().addWidget(button)
        bar.pushWidget(widget, level, 0)

    def _edit_published(self, local, widget):
        self.iface.messageBar().popWidget(widget)
        if local is not None:
            self.edit_layer(local)

    def open_from_services(self, org_id, service, row, edit):
        """The Services tab opens a layer of a service, for reading or editing (CA-5)."""
        if not self._attached:
            self._message(
                tr("Opening a feature service from QGIS needs QGIS 3.36 or later. Its OGC API Features address: {}").format(
                    config.feature_service_ogc_url(org_id, service["serviceId"])
                ),
                _level("Warning"),
            )
            return
        ref = service_memory.ServiceRef(
            org_id,
            service["serviceId"],
            row["layerId"],
            row["title"],
            row.get("srid"),
            service_memory.SERVICE,
            can_edit=bool(service.get("canEdit")),
        )
        layer = self.open(ref, read_only=not edit)
        if layer is None:
            return
        self.iface.setActiveLayer(layer)
        if edit:
            layer.startEditing()
            text = tr("“{}” is in editing mode: save to write the changes to ALIGN.").format(layer.name())
        else:
            text = tr("“{}” is open for reading.").format(layer.name())
        self._message(text, _level("Success"))

    # -- the session (CA-11) ------------------------------------------------

    def _keep_fresh(self):
        if service_layer.service_layers():
            self.session.keep_signer_fresh()

    def _on_project_read(self, *_args):
        layers = service_layer.service_layers()
        if not layers:
            return
        if self.session.keep_signer_fresh():
            self._reload([layer for layer, _ref in layers if not layer.isValid()])
            self._refresh_rules([layer for layer, _ref in layers])
            return
        names = [layer.name() for layer, _ref in layers]
        text = (
            tr("Sign in to ALIGN to read this layer: “{}”.").format(names[0])
            if len(names) == 1
            else tr("Sign in to ALIGN to read these {} layers.").format(len(names))
        )
        bar = self.iface.messageBar()
        widget = bar.createMessage(PLUGIN_NAME, text)
        button = QPushButton(tr("Sign in…"))
        button.clicked.connect(lambda _checked=False, w=widget: self._sign_in_from(w))
        widget.layout().addWidget(button)
        bar.pushWidget(widget, _level("Warning"), 0)

    def _sign_in_from(self, widget):
        self.iface.messageBar().popWidget(widget)
        self.session.sign_in()

    def _on_signed_in(self):
        self._reload_all()

    def _reload_all(self):
        layers = [layer for layer, _ref in service_layer.service_layers()]
        self._reload(layers)
        self._refresh_rules(layers)

    def _reload(self, layers):
        if not layers:
            return
        self.session.keep_signer_fresh()
        still = [layer.name() for layer in layers if not service_layer.reload_layer(layer)]
        if still:
            self._message(tr("ALIGN still refuses these layers: {}.").format(", ".join(still)), _level("Warning"))


def _clause(text):
    """Words set inside a sentence: the sentence ends them, so their own final
    period goes (a rule's message often has one)."""
    return text.rstrip().rstrip(".")


def _level(name):
    return qt_enum(Qgis, "MessageLevel", name)


def _vector_type():
    group = getattr(Qgis, "LayerType", None)
    if group is not None and hasattr(group, "Vector"):
        return group.Vector
    return QgsMapLayer.VectorLayer
