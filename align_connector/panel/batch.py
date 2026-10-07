# -*- coding: utf-8 -*-
"""Which layers of the project can be published, and what one click sends.

One rule shapes the list: **every layer of the project is shown, none
hidden.** A layer the plugin cannot send is listed greyed with its reason,
because a silent absence leaves someone wondering why their layer is missing;
a reason does not. The test for "publishable" is the export's own
(`publishable_kind`, and the raster export's refusal of a remote service),
so the list can never offer what the publish would then refuse.

Pure: proven by ``scripts/smoke_panel.py``.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from ..i18n import tr
from ..publish import isometa
from ..publish.exporter import publishable_kind


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
    """A plan, resolved: the layer itself and its briefing (style, metadata)."""

    def __init__(self, layer, plan, briefing):
        self.layer = layer
        self.plan = plan
        self.briefing = briefing


class Batch(object):
    """What one click on Publish sends: the requests, and what they share."""

    def __init__(self, org, visibility, description, capacity_line, requests):
        self.org = org
        self.visibility = visibility
        self.description = description
        self.capacity_line = capacity_line
        self.requests = requests


def rows_of(layers):
    """The project's layers in the order given, each with its verdict."""
    rows = []
    for layer in layers:
        layer_id = _call(layer, "id", "") or ""
        name = _call(layer, "name", "") or layer_id or tr("Layer")
        kind = publishable_kind(layer)
        # The record's own title is a better proposal than the layer name,
        # when someone took the time to write one (TCK-2202).
        title = isometa.read(layer).title if kind else None
        rows.append(Row(layer_id, name, kind, _reason(layer, kind), title))
    return rows


def _reason(layer, kind):
    if kind is None:
        return tr("Not a vector or raster layer.")
    if kind == "raster":
        # The same verdict the raster export gives: a remote service has no
        # bytes to publish. Said here, before the layer is even checked.
        provider = _call(_call(layer, "dataProvider", None), "name", "gdal")
        if provider and provider != "gdal":
            return tr("A {} connection, not a dataset: there is no file to publish.").format(provider.upper())
    return None


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
