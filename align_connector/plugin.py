# -*- coding: utf-8 -*-
"""Plugin lifecycle: what ALIGN Connector adds to QGIS, and what it takes back.

This module owns the lifecycle, the menu state and the hand-off between the
panel and the publish tasks, nothing else. Authentication lives in
:mod:`align_connector.auth`, the panel in :mod:`align_connector.panel`, the
publish itself in :mod:`align_connector.publish`. Keeping this one small is
what makes the plugin reloadable during development without leaving orphaned
menu entries behind.

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

from qgis.core import Qgis, QgsApplication, QgsMapLayer
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from . import config
from .auth import AlignSession
from .panel import webmap
from .panel.camera import canvas_map_view
from .panel.dock import AlignDock, dock_area
from .panel.follow import LotFollower
from .panel.outcome import INFO, SUCCESS, WARNING, Outcome, outcome_of
from .publish.reference_task import RegisterReferenceTask
from .publish.task import PublishLayerTask
from .publish.webmap_task import ComposeWebMapTask
from .qtcompat import run_exec
from .settings_dialog import SettingsDialog

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
        #: The subset that also went to the toolbar. Tracked separately because
        #: removing a toolbar icon that was never added is a silent asymmetry —
        #: harmless today, and exactly the kind of drift that outlives the code
        #: that caused it.
        self._toolbar_actions = []
        #: The entries in a layer's context menu, one per layer type: QGIS
        #: removes one registration per call, so one action per type keeps
        #: additions and removals countable.
        self._layer_actions = []
        self._sign_in_action = None
        self.dock = None
        self.session = AlignSession()
        #: Running publishes. QGIS' task manager does not own the Python
        #: object, so dropping our reference would collect a task mid-upload.
        self._tasks = []
        #: The lot each publish task belongs to, when a web map waits for the lot.
        self._followers = {}

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
        self._add_action(
            self.tr("Publish to ALIGN…"),
            self.run,
            icon="icon.svg",
            status_tip=self.tr("Open the ALIGN panel: publish layers to your Datastore and follow them"),
            toolbar=True,
        )
        self._sign_in_action = self._add_action(
            self.tr("Sign in to ALIGN…"),
            self._toggle_sign_in,
            status_tip=self.tr("Connect QGIS to your ALIGN account"),
        )
        self._add_action(
            self.tr("Settings…"),
            self._open_settings,
            status_tip=self.tr("Which ALIGN this plugin talks to, and who you are on it"),
        )

        # The panel exists from the start, hidden: QGIS can only give a dock
        # back its saved place if the dock is there when the window is laid
        # out. QGIS shows a dock it adds; a tool is opened when asked.
        self.dock = AlignDock(self.session, self._start_batch, self.iface.mainWindow())
        self.iface.addDockWidget(dock_area(), self.dock)
        self.dock.hide()

        # "Publish to ALIGN…" in the context menu of a layer: opens the panel
        # with that layer checked, and nothing else.
        for kind in ("Vector", "Raster"):
            action = QAction(self.tr("Publish to ALIGN…"), self.iface.mainWindow())
            action.setIcon(QIcon(os.path.join(self.plugin_dir, "icon.svg")))
            action.triggered.connect(self._publish_current_layer)
            self.iface.addCustomActionForLayerType(action, None, _layer_type(kind), True)
            self._layer_actions.append(action)

        self.session.signed_in.connect(self._on_signed_in)
        self.session.signed_out.connect(self._on_signed_out)
        self.session.failed.connect(self._on_failed)
        self.session.waiting_for_browser.connect(self._on_waiting)

        self._refresh_state()

    def unload(self):
        """Remove everything :meth:`initGui` added — no more, no less."""
        for action in self._actions:
            self.iface.removePluginWebMenu(MENU_TITLE, action)
        for action in self._toolbar_actions:
            self.iface.removeToolBarIcon(action)
        for action in self._layer_actions:
            self.iface.removeCustomActionForLayerType(action)
        self._actions.clear()
        self._toolbar_actions.clear()
        self._layer_actions.clear()
        self._sign_in_action = None
        if self.dock is not None:
            self.dock.detach()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None

    def _add_action(self, text, callback, icon=None, status_tip=None, toolbar=False):
        action = QAction(text, self.iface.mainWindow())
        if icon:
            action.setIcon(QIcon(os.path.join(self.plugin_dir, icon)))
        if status_tip:
            action.setStatusTip(status_tip)
        action.triggered.connect(callback)
        self.iface.addPluginToWebMenu(MENU_TITLE, action)
        if toolbar:
            self.iface.addToolBarIcon(action)
            self._toolbar_actions.append(action)
        self._actions.append(action)
        return action

    # -- state --------------------------------------------------------------

    def _refresh_state(self):
        """Make the menu and the panel say the truth about the current session.

        The panel is reloaded only when it is open: a hidden panel reading
        the organizations at every QGIS start would be a network call nobody
        asked for.
        """
        signed_in = self.session.is_signed_in()

        if self._sign_in_action is not None:
            account = self.session.account()
            if signed_in:
                self._sign_in_action.setText(
                    self.tr("Sign out of ALIGN ({})").format(account)
                    if account
                    else self.tr("Sign out of ALIGN")
                )
            else:
                self._sign_in_action.setText(self.tr("Sign in to ALIGN…"))

        if self.dock is not None:
            self.dock.refresh(reload=signed_in and self.dock.isVisible())

    # -- session callbacks --------------------------------------------------

    def _toggle_sign_in(self):
        if self.session.is_signed_in():
            self.session.sign_out()
        else:
            self.session.sign_in()

    def _open_settings(self):
        dialog = SettingsDialog(self.session, self.iface.mainWindow())
        run_exec(dialog)
        # The dialog can sign in or out; the menu must not keep saying otherwise.
        self._refresh_state()

    def _on_waiting(self):
        self._message(
            self.tr("Finish signing in from your browser, then come back to QGIS."),
            Qgis.Info,
        )

    def _on_signed_in(self):
        self._refresh_state()
        self._message(self.tr("Connected to ALIGN."), Qgis.Success)

    def _on_signed_out(self):
        self._refresh_state()
        self._message(self.tr("Signed out of ALIGN."), Qgis.Info)

    def _on_failed(self, reason):
        self._refresh_state()
        self._message(reason, Qgis.Warning)

    def _message(self, text, level):
        self.iface.messageBar().pushMessage(PLUGIN_NAME, text, level=level, duration=6)

    # -- entry points -------------------------------------------------------

    def run(self):
        """Open the ALIGN panel. Signed out, it offers to sign in."""
        self.dock.show_panel()

    def _publish_current_layer(self):
        """The context menu's entry: the panel, with this one layer checked."""
        layer = self.iface.layerTreeView().currentLayer() or self.iface.activeLayer()
        self.dock.show_panel()
        if layer is not None:
            self.dock.publish.check_only(layer.id())

    def _start_batch(self, batch):
        """One task per layer; the panel follows each, the bar says they left.

        When the batch asks for a web map, a follower waits for the last task
        and the camera is read now, from what the publisher is looking at.
        """
        org = batch.org
        follower = None
        if batch.web_map_name:
            batch.map_view = canvas_map_view(self.iface.mapCanvas())
            follower = LotFollower(batch, self._on_lot_finished)
        for request in batch.requests:
            if request.briefing.reference is not None:
                # A service is registered, not uploaded (TCK-2207).
                task = RegisterReferenceTask(
                    self.session,
                    request.layer.id(),
                    org["id"],
                    org["name"],
                    request.plan.title,
                    request.briefing.reference,
                    description=batch.description,
                    visibility=batch.visibility,
                )
            else:
                task = PublishLayerTask(
                    self.session,
                    request.layer,
                    org["id"],
                    org["name"],
                    request.plan.title,
                    selected_only=request.plan.selected_only,
                    description=batch.description,
                    visibility=batch.visibility,
                    capacity_line=batch.capacity_line,
                    style=request.briefing.style,
                    iso=request.briefing.iso,
                )
            task.taskCompleted.connect(lambda t=task: self._on_publish_finished(t, True))
            task.taskTerminated.connect(lambda t=task: self._on_publish_finished(t, False))
            self._tasks.append(task)
            if follower is not None:
                follower.started(task, request)
                self._followers[id(task)] = follower
            self.dock.track(task, org["id"])
            QgsApplication.taskManager().addTask(task)

        self.dock.show_activity()
        count = len(batch.requests)
        if count == 1:
            text = self.tr("Publishing “{}” to {} — follow it in the ALIGN panel.").format(
                batch.requests[0].plan.title, org["name"]
            )
        else:
            text = self.tr("Publishing {} layers to {} — follow them in the ALIGN panel.").format(count, org["name"])
        if batch.web_map_name:
            text += " " + self.tr("The web map “{}” will be created once they have all landed.").format(batch.web_map_name)
        self._message(text, Qgis.Info)

    def _on_lot_finished(self, follower):
        """Every layer of the lot has landed, or failed: ask for the web map."""
        batch = follower.batch
        org = batch.org
        plan = webmap.plan(follower.records())
        if not plan.layers:
            self._message(
                self.tr("No layer of the lot was published, so the web map “{}” was not created.").format(batch.web_map_name),
                Qgis.Warning,
            )
            return
        task = ComposeWebMapTask(
            self.session,
            org["id"],
            org["name"],
            batch.web_map_name,
            plan.layers,
            map_view=batch.map_view,
            description=batch.description,
            visibility=batch.visibility,
        )
        left_out = list(plan.left_out)
        task.taskCompleted.connect(lambda t=task: self._on_web_map_finished(t, True, left_out))
        task.taskTerminated.connect(lambda t=task: self._on_web_map_finished(t, False, left_out))
        self._tasks.append(task)
        self.dock.track(
            task, org["id"], title=self.tr("Web map “{}”").format(batch.web_map_name), link=config.web_map_url, busy=True
        )
        QgsApplication.taskManager().addTask(task)

    def _on_web_map_finished(self, task, succeeded, left_out):
        if task in self._tasks:
            self._tasks.remove(task)
        text = webmap.describe_outcome(task, succeeded, left_out)
        outcome = Outcome(SUCCESS if succeeded else WARNING, text, task.asset_id if succeeded else None)
        if self.dock is not None:
            self.dock.settle(task, outcome)
        if not succeeded and task.needs_sign_in:
            self.session.sign_out()
            self._refresh_state()
        self._message(text, _LEVELS.get(outcome.level, Qgis.Warning))

    def _on_publish_finished(self, task, succeeded):
        """Report the outcome, in the words of whoever refused it."""
        if task in self._tasks:
            self._tasks.remove(task)

        outcome = outcome_of(task, succeeded)
        if self.dock is not None:
            self.dock.settle(task, outcome)
        follower = self._followers.pop(id(task), None)
        if follower is not None:
            follower.finished(task, succeeded)

        if not succeeded and task.needs_sign_in:
            # The session died mid-publish; say so once, and leave the menu
            # honest about it.
            self.session.sign_out()
            self._refresh_state()

        self._message(outcome.text, _LEVELS.get(outcome.level, Qgis.Warning))


_LEVELS = {SUCCESS: Qgis.Success, INFO: Qgis.Info}


def _layer_type(name):
    """``Qgis.LayerType.Vector`` (QGIS ≥ 3.30), ``QgsMapLayer.VectorLayer``
    before: the floor is 3.22, the reference 3.44."""
    group = getattr(Qgis, "LayerType", None)
    if group is not None and hasattr(group, name):
        return getattr(group, name)
    return getattr(QgsMapLayer, name + "Layer")
