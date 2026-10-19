# -*- coding: utf-8 -*-
"""What a layer remembers of a feature service (TCK-2348).

Two kinds of layer belong to a service, and both keep it in their custom
properties, saved with the project:

* the **service layer**, opened from the service (QGIS' OAPIF provider):
  right-click offers to edit it, or to read it when the user may not edit;
* the **published original**, the local layer a service was made from:
  right-click offers to edit it in QGIS, which opens the service in its place,
  at any time and not only in the bar that follows the publication (CA-6).

As for the Datastore memory (``memory.py``), this is a claim, not a proof: the
service may be gone, the right withdrawn. ALIGN answers when the layer is
opened or edited.

The names (CA-7): the service layer is "<title> (ALIGN)", and the original is
kept under a group "Local originals", so the two never read the same.

Pure: proven by ``scripts/smoke_service_layers.py`` on stub layers.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from .. import config
from ..i18n import tr

KEY_KIND = "align/serviceKind"
KEY_ORG = "align/serviceOrgId"
KEY_SERVICE = "align/serviceId"
KEY_LAYER = "align/serviceLayerId"
KEY_TITLE = "align/serviceTitle"
KEY_SRID = "align/serviceSrid"
KEY_API = "align/serviceApiUrl"
#: Whether the user could edit the service when last asked: chooses the menu's
#: words ("Edit in QGIS" or "Open read-only"); ALIGN decides again on the click.
KEY_CAN_EDIT = "align/serviceCanEdit"
_KEYS = (KEY_KIND, KEY_ORG, KEY_SERVICE, KEY_LAYER, KEY_TITLE, KEY_SRID, KEY_API, KEY_CAN_EDIT)

#: The layer opened from the service.
SERVICE = "service"
#: The local layer the service was published from.
ORIGINAL = "original"


class ServiceRef(object):
    """One layer of one service of one organization."""

    def __init__(self, org_id, service_id, layer_id, title, srid=None, kind=SERVICE, can_edit=True):
        self.org_id = org_id
        self.service_id = service_id
        self.layer_id = layer_id
        self.title = title
        self.srid = srid
        self.kind = kind
        self.can_edit = can_edit


def remember(layer, ref, kind):
    """Write the service on the layer, as a service layer or as its original."""
    setter = getattr(layer, "setCustomProperty", None)
    if not callable(setter):
        return
    setter(KEY_KIND, kind)
    setter(KEY_ORG, ref.org_id)
    setter(KEY_SERVICE, ref.service_id)
    setter(KEY_LAYER, ref.layer_id)
    setter(KEY_TITLE, ref.title or "")
    setter(KEY_SRID, str(int(ref.srid)) if ref.srid else "")
    setter(KEY_API, config.api_url())
    setter(KEY_CAN_EDIT, "1" if ref.can_edit else "0")


def set_can_edit(layer, can_edit):
    """Record what ALIGN last answered about editing the layer's service."""
    setter = getattr(layer, "setCustomProperty", None)
    if callable(setter):
        setter(KEY_CAN_EDIT, "1" if can_edit else "0")


def read(layer):
    """The layer's service, or ``None``: none, or one of another ALIGN
    deployment than the plugin points at now."""
    getter = getattr(layer, "customProperty", None)
    if not callable(getter):
        return None
    kind = _text(getter(KEY_KIND, ""))
    org_id = _text(getter(KEY_ORG, ""))
    service_id = _text(getter(KEY_SERVICE, ""))
    layer_id = _text(getter(KEY_LAYER, ""))
    if kind not in (SERVICE, ORIGINAL) or not org_id or not service_id or not layer_id:
        return None
    if _text(getter(KEY_API, "")) != config.api_url():
        return None
    srid = _text(getter(KEY_SRID, ""))
    return ServiceRef(
        org_id,
        service_id,
        layer_id,
        _text(getter(KEY_TITLE, "")),
        int(srid) if srid.isdigit() else None,
        kind,
        can_edit=_text(getter(KEY_CAN_EDIT, "1")) != "0",
    )


def forget(layer):
    remover = getattr(layer, "removeCustomProperty", None)
    if not callable(remover):
        return
    for key in _KEYS:
        remover(key)


def service_layer_name(title):
    """The service layer's name: never the original's (CA-7)."""
    return tr("{} (ALIGN)").format(title)


def originals_group_name():
    """The group the originals are kept in, hidden, once their service is open (CA-7)."""
    return tr("Local originals")


def _text(value):
    return value.strip() if isinstance(value, str) else ""
