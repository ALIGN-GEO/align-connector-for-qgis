# -*- coding: utf-8 -*-
"""What the Services tab shows, decided away from Qt (TCK-2348, CA-3, CA-4).

The organization's catalogue, as ArcGIS Pro's catalogue shows a portal's:
the services the user may read, a search by name, and for one service its
sheet (metadata) and its layers. ALIGN decides who may edit; this words it.

Pure: proven by ``scripts/smoke_service_layers.py``.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import unicodedata

from ..i18n import tr


def _folded(text):
    """Case- and accent-insensitive: "Arbres d'alignement" is found by "arbre", "Électricité" by "electr"."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def matching(services, query):
    """The services whose title or description holds every word of the query."""
    words = _folded(query).split()
    if not words:
        return list(services)
    kept = []
    for service in services:
        haystack = _folded("{} {}".format(service.get("title") or "", service.get("description") or ""))
        if all(word in haystack for word in words):
            kept.append(service)
    return kept


def visibility_label(visibility):
    return {
        "private": tr("Private"),
        "group": tr("Shared with groups"),
        "org": tr("Organization"),
        "public": tr("Public"),
    }.get(visibility, visibility or "—")


def access_label(service):
    """What the user may do with the service, as the list says it."""
    return tr("Editable") if service.get("canEdit") else tr("Read only")


def _when(timestamp):
    """``2026-10-18T09:30:00.000Z`` → ``2026-10-18 09:30 UTC``."""
    text = (timestamp or "").strip()
    if len(text) < 16 or text[10] != "T":
        return text or "—"
    return "{} {} UTC".format(text[:10], text[11:16])


def projection_label(layers):
    """The storage projection of the service's layers."""
    srids = sorted({layer.get("srid") for layer in layers if layer.get("srid")})
    if not srids:
        return tr("No geometry")
    return ", ".join("EPSG:{}".format(srid) for srid in srids)


def sheet(service, description):
    """The metadata of a service, as (label, value) lines (CA-4)."""
    layers = description.get("layers") or []
    return [
        (tr("Title"), service.get("title") or description.get("title") or "—"),
        (tr("Description"), service.get("description") or "—"),
        (tr("Visibility"), visibility_label(service.get("visibility"))),
        (tr("Storage projection"), projection_label(layers)),
        (tr("Last change"), _when(service.get("updatedAt"))),
        (tr("Owner"), service.get("ownerName") or "—"),
        (tr("Your access"), access_label(service)),
    ]


_GEOMETRIES = {
    "Point": "Points",
    "MultiPoint": "Points",
    "LineString": "Lines",
    "MultiLineString": "Lines",
    "Polygon": "Polygons",
    "MultiPolygon": "Polygons",
}


def geometry_label(geometry_type):
    word = _GEOMETRIES.get(geometry_type or "")
    if word == "Points":
        return tr("Points")
    if word == "Lines":
        return tr("Lines")
    if word == "Polygons":
        return tr("Polygons")
    return tr("No geometry") if not geometry_type else geometry_type


def count_label(count):
    if not isinstance(count, int) or isinstance(count, bool):
        return "—"
    return tr("1 feature") if count == 1 else tr("{} features").format("{:,}".format(count).replace(",", " "))


def fields_label(fields):
    names = [field.get("alias") or field.get("name") for field in fields or [] if isinstance(field, dict)]
    names = [name for name in names if name]
    return ", ".join(names) if names else tr("No fields")


def layer_rows(description):
    """One row per layer: title, geometry, feature count, fields (CA-4)."""
    rows = []
    for layer in description.get("layers") or []:
        if not isinstance(layer, dict) or not layer.get("layerId"):
            continue
        rows.append(
            {
                "layerId": layer["layerId"],
                "title": layer.get("title") or layer["layerId"],
                "geometry": geometry_label(layer.get("geometryType")),
                "count": count_label(layer.get("featureCount")),
                "fields": fields_label(layer.get("fields")),
                "srid": layer.get("srid"),
            }
        )
    return rows


def may_edit(description):
    """Whether ALIGN lets this user make at least one kind of edit to the service."""
    return bool([kind for kind in description.get("callerMay") or [] if kind in ("add", "update", "delete")])
