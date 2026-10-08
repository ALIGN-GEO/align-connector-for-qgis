# -*- coding: utf-8 -*-
"""Which layers of the project can be published, and what one click sends.

One rule shapes the list: **every layer of the project is shown, none
hidden.** A layer the plugin cannot send is listed greyed with its reason,
because a silent absence leaves someone wondering why their layer is missing;
a reason does not. The test for "publishable" is the export's own
(`publishable_kind`) sharpened by one verdict per layer (`support.verdict`:
provider, geometry, count), so the list can never offer what the publish
would then refuse, and a refusal is read before the send, never after.

Pure: proven by ``scripts/smoke_panel.py``.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from ..i18n import tr
from ..publish import isometa, support


class Row(object):
    """One layer of the project as the panel lists it.

    :param reason: why it cannot be published, or ``None`` when it can
    :param title: the title to propose: the record's own, else the layer name
    """

    def __init__(self, layer_id, name, kind, reason=None, title=None):
        self.layer_id = layer_id
        self.name = name
        self.kind = kind
        self.reason = reason
        self.title = title or name

    @property
    def publishable(self):
        return self.reason is None


class Plan(object):
    """One publication of the batch: a layer, under a title."""

    def __init__(self, layer_id, title, selected_only=False):
        self.layer_id = layer_id
        self.title = title
        self.selected_only = selected_only


class Request(object):
    """A plan, resolved: the layer itself, its briefing (style, metadata), and
    what the web map will need of it (visibility in QGIS, geometry)."""

    def __init__(self, layer, plan, briefing, visible=True, geometry_type=None, source_layers=None, unplaceable=None):
        self.layer = layer
        self.plan = plan
        self.briefing = briefing
        self.visible = visible
        self.geometry_type = geometry_type
        #: Tile layers of an archive, for the web map (TCK-2206).
        self.source_layers = list(source_layers or [])
        #: Why a web map composed here cannot place the item, or None.
        self.unplaceable = unplaceable


class Batch(object):
    """What one click on Publish sends: the requests, what they share, and
    whether a web map should assemble them once they have all landed."""

    def __init__(self, org, visibility, description, capacity_line, requests, web_map_name=None, map_view=None):
        self.org = org
        self.visibility = visibility
        self.description = description
        self.capacity_line = capacity_line
        self.requests = requests
        self.web_map_name = web_map_name
        self.map_view = map_view


def rows_of(layers):
    """The project's layers in the order given, each with its verdict."""
    rows = []
    for layer in layers:
        layer_id = _call(layer, "id", "") or ""
        name = _call(layer, "name", "") or layer_id or tr("Layer")
        verdict = support.verdict(layer)
        # The record's own title is a better proposal than the layer name,
        # when someone took the time to write one (TCK-2202).
        title = isometa.read(layer).title if verdict.publishable else None
        rows.append(Row(layer_id, name, verdict.kind, verdict.reason, title))
    return rows


def describe_blocked(rows):
    """The layers that cannot go, each with its reason, under the list: a
    refusal the publisher reads before the send, never after. Empty when
    every layer can go."""
    blocked = [row for row in rows if not row.publishable]
    if not blocked:
        return ""
    head = tr("1 layer cannot be published:") if len(blocked) == 1 else tr("{} layers cannot be published:").format(len(blocked))
    return head + " " + "; ".join("{} ({})".format(row.name, row.reason) for row in blocked)


def plan(rows, checked_ids, titles=None, selected_only_ids=()):
    """The publications one click sends: the checked, publishable rows, in
    panel order, each under its own title (the proposed one unless renamed)."""
    titles = titles or {}
    checked = set(checked_ids)
    selected = set(selected_only_ids)
    plans = []
    for row in rows:
        if row.layer_id not in checked or not row.publishable:
            continue
        title = (titles.get(row.layer_id) or "").strip() or row.title
        plans.append(Plan(row.layer_id, title, row.layer_id in selected and row.kind == "vector"))
    return plans


def describe_plan(plans):
    """What the Publish button says: how many layers one click will send."""
    count = len(plans)
    if count == 0:
        return tr("Nothing selected")
    if count == 1:
        return tr("Publish 1 layer")
    return tr("Publish {} layers").format(count)


def _call(target, name, default=None):
    if target is None:
        return default
    method = getattr(target, name, None)
    if not callable(method):
        return default
    try:
        return method()
    except Exception:  # noqa: BLE001 — a binding gap reads as unknown, never a crash in a list
        return default
