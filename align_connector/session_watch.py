# -*- coding: utf-8 -*-
"""Learning at once that ALIGN ended the session (TCK-2348).

A revocation in ALIGN's Settings → Integrations takes effect on ALIGN's side
immediately; on this computer the tokens stay valid by their date. Waiting
for the next refresh left the user editing for nothing, under QGIS's raw
errors, for up to an hour (findings E, F and I of the QGIS recipe). Three
watches close that gap, all reading what QGIS already does:

* **A 401 on a signed request** (CA-16): QGIS's network manager reports every
  reply. A 401 from ALIGN's feature services makes the session ask ALIGN, once
  per burst; refused, the session ends with its reason, and the plugin says so
  with its "Sign in…" button. The bearer a 401 answers is never re-sent.
* **QGIS's raw error for a refused ALIGN layer** (CA-19): the provider raises
  "Download of features for layer <id> failed…" per layer. Once the session is
  known to be refused, a message-bar item naming an ALIGN service layer's id is
  taken back: the plugin's one message says what happened.
* **A project opening** (CA-23): before QGIS loads the layers of a project that
  holds ALIGN service layers, the session is checked with ALIGN, so a
  revocation made while QGIS was closed is learnt before the layers ask.

What it cannot do: QGIS's "Handle Unavailable Layers" window still opens for
an ALIGN layer that cannot load signed out. QGIS keeps that handler to itself
(no getter, not exposed to Python), and replacing it would take the window
away from every other layer.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import contextlib
import time
import zipfile

from qgis.core import QgsNetworkAccessManager, QgsProject
from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtNetwork import QNetworkRequest

from . import config
from .auth.request_signer import signable
from .qtcompat import qt_enum

#: One check per burst: a revoked token makes every pending request fail.
_CHECK_EVERY_SEC = 30
#: How long after a refusal QGIS's raw errors for ALIGN layers are taken back.
_QUIET_FOR_SEC = 120
#: What marks a project that holds ALIGN service layers: their OGC address.
_SERVICE_MARK = b"/feature-services/"


def project_mentions_services(path):
    """Whether the project file at ``path`` holds an ALIGN service layer.

    Read before QGIS loads the layers; ``.qgz`` is a zip holding the ``.qgs``.
    An unreadable file says no: QGIS will report it itself.
    """
    if not path:
        return False
    with contextlib.suppress(OSError, zipfile.BadZipFile, KeyError):
        if path.lower().endswith(".qgz"):
            with zipfile.ZipFile(path) as archive:
                return any(_SERVICE_MARK in archive.read(name) for name in archive.namelist() if name.lower().endswith(".qgs"))
        with open(path, "rb") as handle:
            return _SERVICE_MARK in handle.read()
    return False


class SessionWatch:
    """The three watches; attached with the service layers (QGIS 3.36+)."""

    def __init__(self, iface, session, service_layers, on_confirmed=None):
        """:param service_layers: ``() -> [(layer, ServiceRef)]``, the project's ALIGN service layers"""
        self.iface = iface
        self.session = session
        self._service_layers = service_layers
        #: Called when ALIGN confirms the session after a 401 (a token in flight): read the layers again.
        self._on_confirmed = on_confirmed
        self._refused_at = 0.0
        self._checked_at = 0.0
        self._pending = False
        self._attached = False

    def attach(self):
        if self._attached:
            return
        self._attached = True
        QgsNetworkAccessManager.instance().finished.connect(self._on_reply)
        self.iface.messageBar().widgetAdded.connect(self._on_bar_item)
        QgsProject.instance().layerLoaded.connect(self._on_layer_loaded)

    def detach(self):
        if not self._attached:
            return
        self._attached = False
        for signal, slot in (
            (QgsNetworkAccessManager.instance().finished, self._on_reply),
            (self.iface.messageBar().widgetAdded, self._on_bar_item),
            (QgsProject.instance().layerLoaded, self._on_layer_loaded),
        ):
            # Never connected, or already gone with QGIS: nothing left to take back.
            with contextlib.suppress(TypeError, RuntimeError):
                signal.disconnect(slot)

    # -- a 401 on a signed request (CA-16) ------------------------------------

    def _on_reply(self, content):
        status = content.attribute(qt_enum(QNetworkRequest, "Attribute", "HttpStatusCodeAttribute"))
        if status != 401 or not signable(content.request().url().toString(), config.api_url()):
            return
        self._refused_at = time.time()
        if self._pending or time.time() - self._checked_at < _CHECK_EVERY_SEC:
            return
        self._pending = True
        # Not inside QGIS's network callback: the check makes a request of its own.
        QTimer.singleShot(0, self._check)

    def _check(self):
        self._pending = False
        self._checked_at = time.time()
        if not self.session.is_signed_in():
            return
        if self.session.verify() and self._on_confirmed is not None:
            self._on_confirmed()

    # -- QGIS's raw errors for refused ALIGN layers (CA-19) -------------------

    def _on_bar_item(self, item):
        if self.session.is_signed_in() and time.time() - self._refused_at > _QUIET_FOR_SEC:
            return
        text = "{} {}".format(_call(item, "title"), _call(item, "text"))
        ids = {ref.layer_id for _layer, ref in self._service_layers()}
        if any(layer_id and layer_id in text for layer_id in ids):
            bar = self.iface.messageBar()
            QTimer.singleShot(0, lambda: bar.popWidget(item))

    # -- a project opening (CA-23) --------------------------------------------

    def _on_layer_loaded(self, index, count):
        if index != 0 or count == 0 or not self.session.is_signed_in():
            return
        if project_mentions_services(QgsProject.instance().fileName()):
            self.session.verify_if_stale()


def _call(item, name):
    method = getattr(item, name, None)
    value = method() if callable(method) else ""
    return value if isinstance(value, str) else ""
