# -*- coding: utf-8 -*-
"""Plugin lifecycle: what ALIGN Connector adds to QGIS, and what it takes back.

This module owns the lifecycle and the menu state, nothing else. Authentication
lives in :mod:`align_connector.auth`, publishing will live in its own module —
keeping this one small is what makes the plugin reloadable during development
without leaving orphaned menu entries behind.

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

from qgis.core import Qgis, QgsApplication
from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QApplication, QDialog

from .auth import AlignSession
from .publish.dialog import PublishDialog
from .publish.task import PublishLayerTask
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
        self._publish_action = None
        self._sign_in_action = None
        self.session = AlignSession()
        #: Running publishes. QGIS' task manager does not own the Python
        #: object, so dropping our reference would collect a task mid-upload.
        self._tasks = []

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
        self._publish_action = self._add_action(
            self.tr("Publish to ALIGN…"),
            self.run,
            icon="icon.svg",
            status_tip=self.tr("Publish the selected layer to your ALIGN Datastore"),
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
        self._actions.clear()
        self._toolbar_actions.clear()
        self._publish_action = None
        self._sign_in_action = None

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
        """Make the menu say the truth about the current session.

        Publishing stays visible but disabled when signed out: hiding it would
        leave a user wondering whether the plugin does what they installed it
        for (honest affordances beat empty menus).
        """
        signed_in = self.session.is_signed_in()

        if self._publish_action is not None:
            self._publish_action.setEnabled(signed_in)
            self._publish_action.setStatusTip(
                self.tr("Publish the selected layer to your ALIGN Datastore")
                if signed_in
                else self.tr("Sign in to ALIGN first")
            )

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

    # -- session callbacks --------------------------------------------------

    def _toggle_sign_in(self):
        if self.session.is_signed_in():
            self.session.sign_out()
        else:
            self.session.sign_in()

    def _open_settings(self):
        dialog = SettingsDialog(self.session, self.iface.mainWindow())
        _exec(dialog)
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

    # -- entry point --------------------------------------------------------

    def run(self):
        """Ask what to publish, then publish it in the background."""
        if not self.session.is_signed_in():
            self._message(self.tr("Sign in to ALIGN before publishing."), Qgis.Warning)
            return

        dialog = PublishDialog(self.session, self.iface.mainWindow())

        # Listing organizations is one round trip; the wait cursor is what
        # tells the user the click registered.
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            usable = dialog.load_organizations()
        finally:
            QApplication.restoreOverrideCursor()

        if not usable:
            self._message(dialog.load_error, Qgis.Warning)
            return

        if _exec(dialog) != QDialog.Accepted:
            return

        org = dialog.selected_org()
        layer = dialog.selected_layer()
        if org is None or layer is None:
            return

        task = PublishLayerTask(
            self.session,
            layer,
            org["id"],
            org["name"],
            dialog.asset_name(),
            selected_only=dialog.only_selection(),
            description=dialog.description(),
            visibility=dialog.visibility(),
            capacity_line=dialog.capacity_line(),
            style=dialog.style(),
            iso=dialog.iso(),
        )
        task.taskCompleted.connect(lambda t=task: self._on_publish_finished(t, True))
        task.taskTerminated.connect(lambda t=task: self._on_publish_finished(t, False))
        self._tasks.append(task)
        QgsApplication.taskManager().addTask(task)

        self._message(
            self.tr("Publishing “{}” to {} — you can keep working.").format(
                dialog.asset_name(), org["name"]
            ),
            Qgis.Info,
        )

    def _on_publish_finished(self, task, succeeded):
        """Report the outcome, in the words of whoever refused it."""
        if task in self._tasks:
            self._tasks.remove(task)

        if succeeded:
            # A failed server-side conversion is not a success worth
            # celebrating: the file is stored, but nothing can display it.
            # Announcing "it is now in ALIGN" would be true and useless.
            if task.derivation_error:
                self._message(
                    self.tr(
                        "“{}” was stored in {}, but ALIGN could not prepare it for display: {} "
                        "The file is safe in your Datastore."
                    ).format(task.asset_name, task.org_name, task.derivation_error),
                    Qgis.Warning,
                )
                return

            message = self.tr("“{}” is now in {} on ALIGN.").format(task.asset_name, task.org_name)
            if task.flattened_fields:
                # The values are all there, but they changed shape. Saying so
                # beats letting someone discover it in the Datastore.
                message += " " + self.tr(
                    "Complex fields were stored as text: {}."
                ).format(", ".join(task.flattened_fields))
            if task.servable_asset_id:
                # The reprojection kept the original and produced a second
                # asset. Two objects now count against the organization's
                # storage, and the one that opens is not the one just created.
                message += " " + self.tr(
                    "It had to be reprojected, so your original was kept and a second, "
                    "display-ready copy was added — both use storage."
                )
            elif task.gis_format == "cog":
                message += " " + self.tr("It was converted to a Cloud-Optimized GeoTIFF.")
            if task.locked_fields:
                # The organization enforces these sheet fields: the publish
                # could not set them, and saying so beats a sheet that quietly
                # differs from what QGIS holds (TCK-2202).
                message += " " + self.tr(
                    "The {} on the item's sheet are set by your organization and were kept as is."
                ).format(", ".join(task.locked_fields))
            self._message(message, Qgis.Success)
            return

        if task.was_cancelled:
            self._message(self.tr("Publishing cancelled. Nothing was catalogued."), Qgis.Info)
            return

        if task.needs_sign_in:
            # The session died mid-publish; say so once, and leave the menu
            # honest about it.
            self.session.sign_out()
            self._refresh_state()

        self._message(task.error_message or self.tr("Publishing failed."), Qgis.Warning)


def _exec(dialog):
    """Show a dialog modally on both Qt bindings.

    PyQt5 named it ``exec_`` (``exec`` was a keyword in the Python 2 days);
    PyQt6 dropped the underscore.
    """
    runner = getattr(dialog, "exec", None) or getattr(dialog, "exec_")
    return runner()
