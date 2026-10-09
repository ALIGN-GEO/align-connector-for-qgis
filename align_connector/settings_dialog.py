# -*- coding: utf-8 -*-
"""Where the plugin talks, and who it talks as (TCK-1667).

Three questions, one screen: *am I connected, as whom, and to which ALIGN?*

Until this existed the answers lived in a `.ini` file the user was expected to
edit by hand — workable for the developer who wrote it, and for nobody else.

What this screen does **not** do is decide what the user may publish. That is
the organization's call and the server's answer (TCK-1666). Here we configure
where the plugin points, and show what it currently is.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
)

from . import config
from . import net
from .i18n import tr
from .metadata import plugin_version
from .qtcompat import qt_enum


class SettingsDialog(QDialog):
    """The plugin's own settings, and its connection state."""

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setWindowTitle(tr("ALIGN Connector — Settings"))
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)

        # -- who am I ------------------------------------------------------
        account_box = QGroupBox(tr("Your ALIGN account"))
        account_layout = QHBoxLayout(account_box)
        self.account_label = QLabel()
        self.account_label.setWordWrap(True)
        account_layout.addWidget(self.account_label, 1)
        self.account_button = QPushButton()
        self.account_button.clicked.connect(self._toggle_account)
        account_layout.addWidget(self.account_button, 0)
        layout.addWidget(account_box)

        # -- which ALIGN ---------------------------------------------------
        # Under "Advanced", folded (TCK-2216): a user talks to aligngeo.com and
        # has no address to type. The fields show a value only when one was
        # set, so a workstation on the production service shows none at all;
        # a custom address keeps the section open, so it is never a surprise.
        custom_app = config.app_url() if config.app_url() != config.DEFAULT_APP_URL else ""
        custom_api = config.api_url() if config.api_url() != config.DEFAULT_API_URL else ""
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText(tr("Advanced"))
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setAutoRaise(True)
        self.advanced_toggle.setToolButtonStyle(qt_enum(Qt, "ToolButtonStyle", "ToolButtonTextBesideIcon"))
        self.advanced_toggle.setArrowType(qt_enum(Qt, "ArrowType", "RightArrow"))
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        layout.addWidget(self.advanced_toggle)

        server_box = QGroupBox(tr("ALIGN server"))
        server_layout = QFormLayout(server_box)

        self.app_edit = QLineEdit(custom_app)
        self.app_edit.setPlaceholderText(config.DEFAULT_APP_URL)
        server_layout.addRow(tr("Application"), self.app_edit)

        self.api_edit = QLineEdit(custom_api)
        self.api_edit.setPlaceholderText(config.DEFAULT_API_URL)
        server_layout.addRow(tr("API"), self.api_edit)

        hint = QLabel(
            tr("Only for another ALIGN deployment (staging, self-hosted). Leave both empty "
               "to use aligngeo.com.")
        )
        hint.setWordWrap(True)
        hint.setEnabled(False)
        server_layout.addRow("", hint)

        test_row = QHBoxLayout()
        self.test_button = QPushButton(tr("Test connection"))
        self.test_button.clicked.connect(self._test_connection)
        test_row.addWidget(self.test_button, 0)
        self.test_label = QLabel()
        self.test_label.setWordWrap(True)
        self.test_label.setTextFormat(qt_enum(Qt, "TextFormat", "PlainText"))
        test_row.addWidget(self.test_label, 1)
        server_layout.addRow("", test_row)

        layout.addWidget(server_box)
        self.server_box = server_box
        self.advanced_toggle.setChecked(bool(custom_app or custom_api))
        self._toggle_advanced(self.advanced_toggle.isChecked())

        version = QLabel("ALIGN Connector {}".format(plugin_version()))
        version.setEnabled(False)
        layout.addWidget(version)

        buttons = QDialogButtonBox(
            qt_enum(QDialogButtonBox, "StandardButton", "Save") | qt_enum(QDialogButtonBox, "StandardButton", "Cancel"),
            self,
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_account()

    # -- account ------------------------------------------------------------

    def _refresh_account(self):
        if self.session.is_signed_in():
            account = self.session.account()
            self.account_label.setText(
                tr("Connected{}").format(tr(" as {}").format(account) if account else "")
            )
            self.account_button.setText(tr("Sign out"))
        else:
            self.account_label.setText(
                tr("Not connected. Signing in opens your browser, where you approve the "
                   "connection in ALIGN.")
            )
            self.account_button.setText(tr("Sign in…"))

    def _toggle_account(self):
        # Saving first: signing in against the URLs shown, not the ones stored
        # before the user edited them, is the only behaviour that is not
        # surprising.
        self._apply_endpoints()
        if self.session.is_signed_in():
            self.session.sign_out()
        else:
            self.session.sign_in()
        self._refresh_account()

    # -- server -------------------------------------------------------------

    def _toggle_advanced(self, shown):
        self.server_box.setVisible(shown)
        self.advanced_toggle.setArrowType(qt_enum(Qt, "ArrowType", "DownArrow" if shown else "RightArrow"))
        self.adjustSize()

    def _apply_endpoints(self):
        config.set_endpoints(
            self.app_edit.text().strip() or config.DEFAULT_APP_URL,
            self.api_edit.text().strip() or config.DEFAULT_API_URL,
        )

    def _test_connection(self):
        """Ask ALIGN who this client is — the one call that needs no session.

        It answers three questions at once: the URL resolves, the API is an
        ALIGN, and this plugin is allowlisted on it. That is the difference
        between "it does not work" and "the address is wrong".
        """
        self._apply_endpoints()
        self.test_button.setEnabled(False)
        self.test_label.setText(tr("Contacting ALIGN…"))
        try:
            result = net.get_json(config.client_endpoint())
            if result.ok:
                name = result.body.get("name") if isinstance(result.body, dict) else None
                self.test_label.setText(
                    tr("Reached ALIGN — it recognises this plugin{}.").format(
                        tr(" as “{}”").format(name) if name else ""
                    )
                )
            elif result.status == net.NO_RESPONSE:
                self.test_label.setText(
                    tr("No answer from that address. Check the API URL, and that you are online.")
                )
            elif result.status == 404:
                self.test_label.setText(
                    tr("That address answered, but does not know this plugin. It may be an "
                       "older ALIGN, or the wrong server.")
                )
            else:
                self.test_label.setText(
                    tr("ALIGN answered {} — {}").format(result.status, result.message(tr("unexpected error")))
                )
        finally:
            self.test_button.setEnabled(True)

    # -- save ---------------------------------------------------------------

    def _save(self):
        self._apply_endpoints()
        self.accept()
