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
from ..publish import isometa, memory, support


class Row(object):
    """One layer of the project as the panel lists it.

    :param reason: why it cannot be published, or ``None`` when it can
    :param title: the title to propose: the record's own, else the layer name
    :param link: the item the layer was already published as (TCK-2218), or None
    """

    def __init__(self, layer_id, name, kind, reason=None, title=None, link=None):
        self.layer_id = layer_id
        self.name = name
        self.kind = kind
        self.reason = reason
        self.title = title or name
        self.link = link

    @property
    def publishable(self):
        return self.reason is None


class Plan(object):
    """One publication of the batch: a layer, under a title — replacing the
    item it was published as (`replace`, a :class:`memory.Link`), or new;
    `as_service`: a vector layer ALIGN turns into an editable feature service
    (TCK-2320), always a new item."""

    def __init__(self, layer_id, title, selected_only=False, replace=None, as_service=False):
        self.layer_id = layer_id
        self.title = title
        self.selected_only = selected_only
        self.replace = replace
        self.as_service = as_service


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
    whether a web map should assemble them once they have all landed.

    :param service_title: the name of the one feature service the checked
        vector layers make (TCK-2346); ``None``: one service per layer
    """

    def __init__(self, org, visibility, description, capacity_line, requests, web_map_name=None, map_view=None, service_title=None):
        self.org = org
        self.visibility = visibility
        self.description = description
        self.capacity_line = capacity_line
        self.requests = requests
        self.web_map_name = web_map_name
        self.map_view = map_view
        self.service_title = service_title

    @property
    def as_service(self):
        """Whether one of the layers goes as a feature service."""
        return bool(self.service_requests)

    @property
    def service_requests(self):
        """The requests whose layer becomes a feature service, in panel order."""
        return [r for r in self.requests if getattr(r.plan, "as_service", False)]

    @property
    def one_service_title(self):
        """The name of the one service the layers make, or ``None`` for one
        service per layer: a single layer is its own service either way."""
        return self.service_title if self.service_title and len(self.service_requests) > 1 else None


def rows_of(layers):
    """The project's layers in the order given, each with its verdict."""
    rows = []
    for layer in layers:
        layer_id = _call(layer, "id", "") or ""
        name = _call(layer, "name", "") or layer_id or tr("Layer")
        verdict = support.verdict(layer)
        # The record's own title is a better proposal than the layer name,
        # when someone took the time to write one (TCK-2202); the title the
        # item already has on ALIGN beats both (TCK-2218).
        link = memory.read(layer) if verdict.publishable else None
        title = (link.title if link is not None else None) or (isometa.read(layer).title if verdict.publishable else None)
        rows.append(Row(layer_id, name, verdict.kind, verdict.reason, title, link))
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


def plan(rows, checked_ids, titles=None, selected_only_ids=(), org_id=None, publish_as_new=(), as_service=False):
    """The publications one click sends: the checked, publishable rows, in
    panel order, each under its own title (the proposed one unless renamed).

    A row that remembers an item in the organization the batch goes to is
    replaced in place (TCK-2218), unless the publisher asked for a new item
    (`publish_as_new`); an item remembered in another organization is not
    this organization's to replace, so the layer goes there as new.

    With `as_service` (TCK-2320), every vector layer goes as an editable
    feature service: a new service each time, never the replacement of a
    file; the other kinds publish as they would without it.
    """
    titles = titles or {}
    checked = set(checked_ids)
    selected = set(selected_only_ids)
    as_new = set(publish_as_new)
    plans = []
    for row in rows:
        if row.layer_id not in checked or not row.publishable:
            continue
        title = (titles.get(row.layer_id) or "").strip() or row.title
        service = bool(as_service) and row.kind == "vector"
        replace = replaceable(row, org_id) if row.layer_id not in as_new and not service else None
        plans.append(Plan(row.layer_id, title, row.layer_id in selected and row.kind == "vector", replace, service))
    return plans


def replaceable(row, org_id):
    """The item a row would replace in this organization, or None."""
    link = getattr(row, "link", None)
    if link is None or org_id is None or link.org_id != org_id:
        return None
    return link


def describe_plan(plans, one_service=False):
    """What the Publish button says: how many layers one click will send,
    how many of them replace an item already on ALIGN, and how many become
    feature services; with `one_service` (TCK-2346), that those make one."""
    count = len(plans)
    replaced = sum(1 for p in plans if getattr(p, "replace", None) is not None)
    services = sum(1 for p in plans if getattr(p, "as_service", False))
    if count == 0:
        return tr("Nothing selected")
    if services > 1 and one_service:
        if services == count:
            return tr("Publish {} layers as one feature service").format(count)
        if replaced == 0:
            return tr("Publish {} layers ({} in one feature service)").format(count, services)
        return tr("Publish {} layers ({} in one feature service, {} replaced)").format(count, services, replaced)
    if services:
        if services == count:
            return tr("Publish 1 layer as a feature service") if count == 1 else tr(
                "Publish {} layers as feature services"
            ).format(count)
        if replaced == 0:
            return tr("Publish {} layers ({} as feature services)").format(count, services)
        return tr("Publish {} layers ({} as feature services, {} replaced)").format(count, services, replaced)
    if count == 1:
        return tr("Replace 1 layer") if replaced == 1 else tr("Publish 1 layer")
    if replaced == 0:
        return tr("Publish {} layers").format(count)
    if replaced == count:
        return tr("Replace {} layers").format(count)
    return tr("Publish {} layers ({} replaced)").format(count, replaced)


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
