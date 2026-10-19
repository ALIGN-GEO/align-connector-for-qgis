# -*- coding: utf-8 -*-
"""Learning at once that ALIGN ended the session (TCK-2348).

A revocation in ALIGN's Settings → Integrations takes effect on ALIGN's side
immediately; on this computer the tokens stay valid by their date. Waiting
for the next refresh left the user editing for nothing, under QGIS's raw
errors, for up to an hour (findings E, F and I of the QGIS recipe). Three
watches close that gap, and a fourth explains a save ALIGN refused, all
reading what QGIS already does:

* **A 401 on a signed request** (CA-16): QGIS's network manager reports every
  reply. A 401 from ALIGN's feature services makes the session ask ALIGN, once
  per burst; refused, the session ends with its reason, and the plugin says so
  with its "Sign in…" button. The bearer a 401 answers is never re-sent.
* **QGIS's raw error for a refused ALIGN layer** (CA-19): QGIS pushes "Layer
  <name>" with the provider's message ("Download of features for layer <id>
  failed…", "Cannot modify feature of id N"), and "Could not commit changes to
  layer <name>". Once the session is known to be refused, a message-bar item
  naming an ALIGN service layer, by its name or its id, is taken back: the
  plugin's one message says what happened. The bar's signal hands Python a
  plain ``QWidget``: the item is cast back to QGIS's ``QgsMessageBarItem``
  to be read (defect 61: read as a ``QWidget``, no message was ever taken
  back).
* **A project opening** (CA-23): before QGIS loads the layers of a project that
  holds ALIGN service layers, the session is checked with ALIGN, so a
  revocation made while QGIS was closed is learnt before the layers ask.
* **A save ALIGN refused for its rules** (TCK-2343, CA-6): QGIS's bar only
  says "Could not commit changes to layer <name>", and for a changed feature
  "Cannot modify feature of id N", nothing of the 422 (defect 62). ALIGN's
  422 on a layer's items is therefore noted from the reply itself, with
  ALIGN's answer when the reply lets it be read; QGIS's messages naming that
  layer just after are taken back, and the plugin explains the refusal once.
  A layer's commit errors that carry ALIGN's answer or the 422's reason
  phrase count as well.

What it cannot do: QGIS's "Handle Unavailable Layers" window still opens for
an ALIGN layer that cannot load signed out. QGIS keeps that handler to itself
(no getter, not exposed to Python), and replacing it would take the window
away from every other layer.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import contextlib
import re
import time
import zipfile

from qgis.core import QgsNetworkAccessManager, QgsProject
from qgis.gui import QgsMessageBarItem
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtNetwork import QNetworkRequest

from . import config
from .auth.request_signer import signable
from .form_rules import rules_refusal
from .qtcompat import qt_enum

#: One check per burst: a revoked token makes every pending request fail.
_CHECK_EVERY_SEC = 30
#: How long after a refusal QGIS's raw errors for ALIGN layers are taken back.
_QUIET_FOR_SEC = 120
#: What marks a project that holds ALIGN service layers: their OGC address.
_SERVICE_MARK = b"/feature-services/"
#: How long QGIS's messages naming a layer are taken back after ALIGN refused its save for its rules.
_RULES_WINDOW_SEC = 30
#: The layer (collection) a write on a service's items is for.
_ITEMS_OF = re.compile(r"/collections/([^/?#]+)/items")


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
    """The four watches; attached with the service layers (QGIS 3.36+)."""

    def __init__(self, iface, session, service_layers, on_confirmed=None, on_rules_refusal=None):
        """:param service_layers: ``() -> [(layer, ServiceRef)]``, the project's ALIGN service layers"""
        self.iface = iface
        self.session = session
        self._service_layers = service_layers
        #: Called when ALIGN confirms the session after a 401 (a token in flight): read the layers again.
        self._on_confirmed = on_confirmed
        #: Called with the layer when ALIGN refused its save for its rules (422): explain it.
        self._on_rules_refusal = on_rules_refusal
        self._refused_at = 0.0
        #: ``{layer_id: {"at", "answer", "explained"}}``: ALIGN's last refusals for the rules (422).
        self._rules_refused = {}
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
        url = content.request().url().toString()
        if status == 422 and signable(url, config.api_url()):
            self._note_rules_refusal(url, content)
            return
        if status != 401 or not signable(url, config.api_url()):
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
        # Judged on the next turn of the event loop: QGIS pushes its messages
        # about a save while saving, and the reply that explains them (ALIGN's
        # 422) reaches the plugin only then; QGIS is done with the save too.
        QTimer.singleShot(0, lambda: self._judge_bar_item(item))

    def _judge_bar_item(self, widget):
        item = _bar_item(widget)
        if item is None or _call(item, "title") == config.PLUGIN_NAME:
            # The plugin's own messages name ALIGN layers too, and say what happened (defect 66).
            return
        text = "{} {}".format(_call(item, "title"), _call(item, "text"))
        bar = self.iface.messageBar()
        refused = self._refused_for_rules(text)
        if refused is not None:
            layer, answer, first = refused
            bar.popWidget(item)
            if first:
                self._on_rules_refusal(layer, answer)
            return
        if self.session.is_signed_in() and time.time() - self._refused_at > _QUIET_FOR_SEC:
            return
        if any(_names(text, layer, ref) for layer, ref in self._service_layers()):
            bar.popWidget(item)

    # -- a save ALIGN refused for its rules (TCK-2343, CA-6) ------------------

    def _note_rules_refusal(self, url, content):
        """ALIGN refused a write on a layer's items for its rules: noted, with
        its answer when the reply lets it be read."""
        match = _ITEMS_OF.search(url)
        if match is None:
            return
        body = ""
        with contextlib.suppress(AttributeError, TypeError):
            body = bytes(content.content()).decode("utf-8", "replace")
        answer = rules_refusal(["Server response: " + body])[1] if body else None
        self._rules_refused[match.group(1)] = {"at": time.time(), "answer": answer, "explained": False}

    def _refused_for_rules(self, text):
        """The ALIGN service layer a bar message names just after ALIGN refused
        its save for its rules: ``(layer, ALIGN's answer or None, whether to
        explain it)``, the explanation given once per refusal."""
        if self._on_rules_refusal is None:
            return None
        now = time.time()
        for layer, ref in self._service_layers():
            if not _names(text, layer, ref):
                continue
            noted = self._rules_refused.get(ref.layer_id)
            if noted is not None and now - noted["at"] > _RULES_WINDOW_SEC:
                noted = None
            refused, answer = rules_refusal(layer.commitErrors())
            if noted is None:
                if not refused:
                    continue
                noted = self._rules_refused[ref.layer_id] = {"at": now, "answer": None, "explained": False}
            first = not noted["explained"]
            noted["explained"] = True
            return layer, noted["answer"] or answer, first
        return None

    # -- a project opening (CA-23) --------------------------------------------

    def _on_layer_loaded(self, index, count):
        if index != 0 or count == 0 or not self.session.is_signed_in():
            return
        if project_mentions_services(QgsProject.instance().fileName()):
            self.session.verify_if_stale()


def _names(text, layer, ref):
    """Whether a message names an ALIGN service layer, by its name or its id."""
    name = layer.name()
    return bool((name and name in text) or (ref.layer_id and ref.layer_id in text))


def _bar_item(widget):
    """The bar item behind what the bar's signal passed: QGIS declares a
    ``QgsMessageBarItem``, Python receives a ``QWidget``. ``None`` once QGIS
    has removed it."""
    if isinstance(widget, sip.simplewrapper):
        if sip.isdeleted(widget):
            return None
        if not isinstance(widget, QgsMessageBarItem) and widget.inherits("QgsMessageBarItem"):
            return sip.cast(widget, QgsMessageBarItem)
    return widget


def _call(item, name):
    method = getattr(item, name, None)
    value = method() if callable(method) else ""
    return value if isinstance(value, str) else ""
