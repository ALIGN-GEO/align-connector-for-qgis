# -*- coding: utf-8 -*-
"""From a finished lot to the web map that shows it (TCK-2204).

The layers left one by one; when the last one has landed, the web map that
assembles them is asked for: the successful items only (the display-ready
copy for a raster), in the Layers panel's order, each with its visibility in
QGIS, its geometry and the style that was sent, and the camera the canvas
showed. Pure: proven by ``scripts/smoke_webmap.py``.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import math

from ..i18n import tr

#: MapLibre's world is 512 px wide at zoom 0.
_TILE = 512.0
_MAX_ZOOM = 22.0


class WebMapPlan(object):
    """What to ask ALIGN for, and what will be missing from it."""

    def __init__(self, layers, left_out):
        #: Request entries, top of the map first.
        self.layers = layers
        #: ``(title, reason)`` of the layers the map will not show.
        self.left_out = left_out


def plan(records):
    """Turn the lot's records into the composition request.

    :param records: ``(request, task, succeeded)`` triples in panel order,
        where ``request`` carries ``plan.title``, ``visible``,
        ``geometry_type`` and ``briefing.style``
    """
    layers = []
    left_out = []
    for request, task, succeeded in records:
        title = request.plan.title
        asset_id = getattr(task, "servable_asset_id", None) or getattr(task, "asset_id", None)
        if not succeeded or getattr(task, "derivation_error", None) or not asset_id:
            left_out.append((title, tr("its publication did not succeed")))
            continue
        unplaceable = getattr(request, "unplaceable", None)
        if unplaceable:
            left_out.append((title, unplaceable))
            continue
        entry = {"assetId": asset_id, "title": title, "visible": bool(request.visible)}
        if request.geometry_type:
            entry["geometryType"] = request.geometry_type
        source_layers = getattr(request, "source_layers", None)
        if source_layers:
            entry["sourceLayers"] = list(source_layers)
        style = getattr(request.briefing, "style", None)
        if style:
            entry["style"] = style
        layers.append(entry)
    return WebMapPlan(layers, left_out)


def map_view_of(bounds, width_px, height_px):
    """The camera that shows ``bounds`` (west, south, east, north, in degrees)
    on a canvas of that size: its centre, and the Web Mercator zoom at which
    the extent fits. None when the extent is degenerate."""
    west, south, east, north = bounds
    lon_span = east - west
    lat_span = north - south
    if width_px <= 0 or height_px <= 0 or lon_span <= 0 or lat_span <= 0:
        return None
    south = max(-85.0, min(85.0, south))
    north = max(-85.0, min(85.0, north))
    if north <= south:
        return None
    zoom_lon = math.log2(width_px * 360.0 / (_TILE * min(lon_span, 360.0)))
    mercator_span = _mercator(north) - _mercator(south)
    zoom_lat = math.log2(height_px * 2.0 * math.pi / (_TILE * mercator_span))
    zoom = max(0.0, min(_MAX_ZOOM, min(zoom_lon, zoom_lat)))
    return {"center": [round(west + lon_span / 2.0, 6), round((south + north) / 2.0, 6)], "zoom": round(zoom, 2)}


def describe_outcome(task, succeeded, left_out):
    """What the Activity view and the message bar say when the web map is done."""
    name = getattr(task, "name", None) or tr("Web map")
    org = getattr(task, "org_name", None) or "ALIGN"
    if not succeeded:
        return getattr(task, "error_message", None) or tr("The web map could not be created.")
    placed = len(getattr(task, "placed", []) or [])
    text = (tr("Web map “{}” is ready in {} with {} layer.") if placed == 1 else tr("Web map “{}” is ready in {} with {} layers.")).format(name, org, placed)
    missing = []
    for entry in getattr(task, "skipped", []) or []:
        missing.append("{} ({})".format(entry.get("title") or entry.get("assetId"), entry.get("reason")))
    for title, reason in left_out:
        missing.append("{} ({})".format(title, reason))
    if missing:
        text += " " + tr("Not placed: {}.").format(", ".join(missing))
    return text


def _mercator(lat_deg):
    lat = math.radians(lat_deg)
    return math.log(math.tan(math.pi / 4.0 + lat / 2.0))
