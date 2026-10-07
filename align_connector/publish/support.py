# -*- coding: utf-8 -*-
"""Can ALIGN's Map builder show this layer, once published? One verdict per layer.

A project holds more than vectors and rasters: meshes, point clouds, vector
tiles, annotations, tables without geometry, layers that are a connection to
a remote service rather than a dataset. The list in the panel shows every
one of them, and for each one that cannot go, the reason, in words a GIS
specialist acts on, so a refusal is never discovered after the send.

The type gate is the export's own (`publishable_kind`); this module adds
what the type alone does not say: the provider, the geometry, the count.
Pure given a layer that answers the QGIS method names; proven by
``scripts/smoke_panel.py``.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from qgis.core import QgsWkbTypes

from ..i18n import tr
from .exporter import publishable_kind

#: Layer classes the plugin does not publish, with what to call them.
_UNSUPPORTED_CLASSES = {
    "QgsMeshLayer": "mesh",
    "QgsPointCloudLayer": "point cloud",
    "QgsVectorTileLayer": "vector tile",
    "QgsAnnotationLayer": "annotation",
    "QgsTiledSceneLayer": "tiled scene (3D tiles)",
    "QgsPluginLayer": "plugin",
    "QgsGroupLayer": "group",
}

#: Vector providers whose features live on a remote service.
_REMOTE_VECTOR = {
    "wfs": "WFS",
    "oapif": "OGC API Features",
    "arcgisfeatureserver": "ArcGIS Feature Service",
}

#: Raster providers that are a connection, not a file.
_REMOTE_RASTER = {
    "wcs": "WCS",
    "arcgismapserver": "ArcGIS Map Service",
    "arcgisimageserver": "ArcGIS Image Service",
}

#: QGIS geometry types by value (QgsWkbTypes.GeometryType and Qgis.GeometryType agree).
_GEOMETRY_NAMES = {0: "Point", 1: "Line", 2: "Polygon", 3: "Unknown", 4: "Null"}


class Verdict(object):
    """What the panel says about one layer.

    :param kind: ``'vector'``, ``'raster'`` or ``None``
    :param reason: why it cannot be published, or ``None`` when it can
    :param notes: sentences the briefing adds (what changes on the way)
    """

    def __init__(self, kind, reason=None, notes=None):
        self.kind = kind
        self.reason = reason
        self.notes = notes or []

    @property
    def publishable(self):
        return self.reason is None


def verdict(layer):
    kind = publishable_kind(layer)
    if kind is None:
        return Verdict(None, _why_not_a_dataset(layer))
    if kind == "vector":
        return _vector_verdict(layer)
    return _raster_verdict(layer)


def geometry_kind(layer):
    """``'Point'``, ``'Line'``, ``'Polygon'``, ``'Unknown'`` or ``'Null'``,
    whichever binding answers (an int on QGIS 3, an enum member on QGIS 4)."""
    value = _call(layer, "geometryType", None)
    if value is None:
        return "Unknown"
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name.replace("Geometry", "") or "Unknown"
    try:
        return _GEOMETRY_NAMES.get(int(value), "Unknown")
    except (TypeError, ValueError):
        return "Unknown"


def geojson_geometry_type(layer):
    """The GeoJSON type ALIGN derives a paint family from, or None."""
    return {"Point": "Point", "Line": "LineString", "Polygon": "Polygon"}.get(geometry_kind(layer))


def _why_not_a_dataset(layer):
    label = _UNSUPPORTED_CLASSES.get(type(layer).__name__)
    if label:
        return tr("A {} layer: ALIGN's Map builder does not take it from QGIS.").format(label)
    if _call(layer, "isValid", True) is False:
        return tr("QGIS cannot read this layer: its source is invalid.")
    return tr("Not a vector or raster layer.")


def _vector_verdict(layer):
    if geometry_kind(layer) == "Null":
        return Verdict("vector", tr("A table without geometry: there is nothing to draw on a map."))
    if _is_curved(layer):
        return Verdict(
            "vector",
            tr(
                "Curved geometries (arcs) cannot be written to FlatGeobuf. Segmentize the layer "
                "first (Processing: Segmentize by maximum distance)."
            ),
        )
    count = _call(layer, "featureCount", -1)
    if count == 0:
        return Verdict("vector", tr("No features: there is nothing to publish."))
    notes = []
    provider = (_call(_call(layer, "dataProvider", None), "name", "") or "").lower()
    service = _REMOTE_VECTOR.get(provider)
    if service:
        notes.append(
            tr(
                "This layer comes from a {} service: a snapshot of its features is published, "
                "not a live connection."
            ).format(service)
        )
    return Verdict("vector", None, notes)


def _raster_verdict(layer):
    provider = (_call(_call(layer, "dataProvider", None), "name", "gdal") or "gdal").lower()
    if provider != "gdal":
        return Verdict(
            "raster",
            tr(
                "A {} connection, not a dataset: there is no file to publish. Add the service "
                "to ALIGN from the web application instead."
            ).format(_service_label(provider, _call(layer, "source", "") or "")),
        )
    return Verdict(
        "raster",
        None,
        [tr("Raster styling is not transmitted: ALIGN renders the file with its default rendering.")],
    )


def _service_label(provider, source):
    if provider == "wms":
        lowered = source.lower()
        if "type=xyz" in lowered:
            return "XYZ tiles"
        if "tilematrixset=" in lowered:
            return "WMTS"
        return "WMS"
    return _REMOTE_RASTER.get(provider, provider.upper())


def _is_curved(layer):
    wkb = _call(layer, "wkbType", None)
    if wkb is None:
        return False
    try:
        return bool(QgsWkbTypes.isCurvedType(wkb))
    except Exception:  # noqa: BLE001 — a binding gap reads as "not curved", the writer will still say
        return False


def _call(target, name, default=None):
    if target is None:
        return default
    method = getattr(target, name, None)
    if not callable(method):
        return default
    try:
        return method()
    except Exception:  # noqa: BLE001 — a binding gap reads as unknown, never a crash in a verdict
        return default
