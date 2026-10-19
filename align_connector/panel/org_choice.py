# -*- coding: utf-8 -*-
"""Which organizations a tab may act in, greyed with the reason (TCK-2348, CA-17).

ALIGN lists every organization of the user to the plugin, each saying whether
it opens itself to the connector and whether feature services are part of its
plan. An organization the plugin may not use stays in the list, greyed, with
the reason in its tooltip: hidden, it left the user wondering where it went.
ALIGN refuses it at every door anyway; the grey only says so before the click.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from qgis.PyQt.QtCore import Qt

from ..i18n import tr
from ..qtcompat import qt_enum


def connector_refusal(org):
    """Why the plugin may not act in ``org`` at all, or ``None``."""
    connector = org.get("connector") if isinstance(org.get("connector"), dict) else None
    if connector is None or connector.get("allowed", True):
        return None
    if connector.get("reason") == "role":
        return tr("Your role in this organization does not let the ALIGN Connector act there.")
    return tr("This organization does not allow the ALIGN Connector. An owner or admin can turn it on in Settings → Integrations.")


def services_refusal(org):
    """Why the Services tab may not use ``org``, or ``None``."""
    refusal = connector_refusal(org)
    if refusal:
        return refusal
    connector = org.get("connector") if isinstance(org.get("connector"), dict) else None
    if connector is not None and connector.get("featureServices") is False:
        return tr("Feature services are not part of this organization's plan.")
    return None


def fill(box, orgs, label, refusal, keep=None):
    """Fill ``box`` with ``orgs``; those ``refusal`` names are greyed with it.

    The selection stays on ``keep`` when it may be used, else goes to the
    first usable organization. Signals are left to the caller.
    """
    box.clear()
    usable = []
    for org in orgs:
        box.addItem(label(org), org["id"])
        index = box.count() - 1
        reason = refusal(org)
        if reason:
            item = box.model().item(index)
            if item is not None:
                item.setEnabled(False)
            box.setItemData(index, reason, qt_enum(Qt, "ItemDataRole", "ToolTipRole"))
        else:
            usable.append(index)
    kept = box.findData(keep) if keep is not None else -1
    box.setCurrentIndex(kept if kept in usable else (usable[0] if usable else -1))


def choose(box, org_id):
    """Select ``org_id`` when it is in ``box`` and may be used; whether it was."""
    index = box.findData(org_id)
    if index < 0:
        return False
    item = box.model().item(index)
    if item is not None and not item.isEnabled():
        return False
    box.setCurrentIndex(index)
    return True
