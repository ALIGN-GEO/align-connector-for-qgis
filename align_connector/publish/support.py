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
from . import services, tiles, uri
from .exporter import publishable_kind

#: Layer classes that are a service by URL (TCK-2207), with the provider key
#: QGIS decodes their source with.
_SERVICE_CLASSES = ("QgsPointCloudLayer", "QgsTiledSceneLayer")

#: Layer classes the plugin does not publish, with what to call them.
_UNSUPPORTED_CLASSES = {
    "QgsMeshLayer": "mesh",
    "QgsAnnotationLayer": "annotation",
    "QgsTiledSceneLayer": "tiled scene (3D tiles)",
    "QgsPluginLayer": "plugin",
    "QgsGroupLayer": "group",
}

#: Raster providers that are a connection the Map builder cannot read live.
_REMOTE_RASTER = {
    "wcs": "WCS",
    "arcgisimageserver": "ArcGIS Image Service",
}

#: QGIS geometry types by value (QgsWkbTypes.GeometryType and Qgis.GeometryType agree).
_GEOMETRY_NAMES = {0: "Point", 1: "Line", 2: "Polygon", 3: "Unknown", 4: "Null"}


class Verdict(object):
    """What the panel says about one layer.

    :param kind: ``'vector'``, ``'raster'``, ``'archive'``, ``'reference'`` or ``None``
    :param reason: why it cannot be published, or ``None`` when it can
    :param notes: sentences the briefing adds (what changes on the way)
    :param unplaceable: why a web map composed from QGIS cannot place the
        item yet, or ``None`` (the item is still published)
    :param reference: the request that registers the layer as a live
        reference (TCK-2207), when the layer is a service
    """

    def __init__(self, kind, reason=None, notes=None, unplaceable=None, reference=None):
        self.kind = kind
        self.reason = reason
        self.notes = notes or []
        self.unplaceable = unplaceable
        self.reference = reference

    @property
    def publishable(self):
        return self.reason is None


def verdict(layer):
    if type(layer).__name__ in _SERVICE_CLASSES and _call(layer, "isValid", True) is not False:
        return _service_verdict(layer, _call(layer, "providerType", "") or "")
    kind = publishable_kind(layer)
    if kind is None:
        return Verdict(None, _why_not_a_dataset(layer))
    if kind == "vector":
        return _vector_verdict(layer)
    if kind == "archive":
        return _archive_verdict(layer)
    return _raster_verdict(layer)


def _service_verdict(layer, provider, source_layers=None):
    """A layer that is a service becomes a live reference (TCK-2207)."""
    source = _call(layer, "source", "") or ""
    plan = services.describe_service(provider, uri.decode(provider, source), source, source_layers)
    if not plan.publishable:
        return Verdict(None, plan.reason)
    return Verdict("reference", None, plan.notes, reference=plan.body)


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
    provider = (_call(_call(layer, "dataProvider", None), "name", "") or "").lower()
    if provider in services.VECTOR_SERVICE_PROVIDERS:
        # A service is kept as a reference ALIGN reads live, never as a copy
        # (decision Benjamin 2026-10-08, the AGOL model).
        return _service_verdict(layer, provider)
    count = _call(layer, "featureCount", -1)
    if count == 0:
        return Verdict("vector", tr("No features: there is nothing to publish."))
    return Verdict("vector", None, [])


def _raster_verdict(layer):
    provider = (_call(_call(layer, "dataProvider", None), "name", "gdal") or "gdal").lower()
    if provider in services.RASTER_SERVICE_PROVIDERS:
        return _service_verdict(layer, provider)
    if provider != "gdal":
        return Verdict(
            "raster",
            tr(
                "A {} connection: the Map builder cannot read it live, and there is no file to publish."
            ).format(_service_label(provider, _call(layer, "source", "") or "")),
        )
    source = (_call(layer, "source", "") or "").split("|")[0].strip()
    if services.remote_cog_url(source):
        return _service_verdict(layer, "gdal")
    if source.lower().endswith(".mbtiles"):
        # The Datastore takes the archive and derives a PMTiles; the Map
        # builder paints vector tile archives only, so far.
        return Verdict(
            "raster",
            None,
            [
                tr(
                    "This MBTiles archive is sent as it is; ALIGN converts it to PMTiles on arrival. "
                    "The Map builder shows vector tile archives; raster tiles wait for a later version."
                )
            ],
            unplaceable=tr("raster tiles are not displayed by the Map builder yet"),
        )
    if _is_multidimensional(source):
        # ALIGN reads such stores by URL only; from a local file, what can
        # travel is one variable, written as a GeoTIFF (every time step a
        # band) and converted to COG on arrival.
        return Verdict(
            "raster",
            None,
            [
                tr(
                    "A NetCDF, HDF or Zarr store: the variable QGIS shows is written as a GeoTIFF, every "
                    "time step as a band, then converted to COG; the Map builder shows the first band. "
                    "Raster styling is not transmitted."
                )
            ],
        )
    return Verdict(
        "raster",
        None,
        [tr("Raster styling is not transmitted: ALIGN renders the file with its default rendering.")],
    )


def _is_multidimensional(source):
    upper = (source or "").upper()
    if upper.startswith(("NETCDF:", "HDF5:", "HDF4", "ZARR:")):
        return True
    return upper.split("?")[0].endswith((".NC", ".NC4", ".H5", ".HDF", ".HDF5", ".ZARR"))


def _archive_verdict(layer):
    decoded = tiles.decoded_source(layer)
    plan = tiles.archive_plan(decoded)
    location = (decoded.get("path") or decoded.get("url") or "").lower()
    if not plan.publishable and (location.startswith(("http://", "https://")) or (decoded.get("type") or "").lower() == "xyz"):
        # A remote tile source is a service: a live reference (TCK-2207).
        return _service_verdict(layer, "vectortile", tiles.source_layers_of(layer))
    plan = tiles.archive_of(layer)
    if not plan.publishable:
        return Verdict("archive", plan.reason)
    notes = [
        tr("This PMTiles archive is sent as it is.")
        if plan.kind == "pmtiles"
        else tr("This MBTiles archive is sent as it is; ALIGN converts it to PMTiles on arrival.")
    ]
    unplaceable = None
    if not tiles.source_layers_of(layer):
        unplaceable = tr("no tile layer is styled in QGIS, so the web map cannot name what to draw")
        notes.append(
            tr("No tile layer is styled in QGIS: a web map composed here cannot place the archive; add it from the Map builder instead.")
        )
    return Verdict("archive", None, notes, unplaceable)


def _service_label(provider, source):
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
