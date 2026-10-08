# -*- coding: utf-8 -*-
"""A QGIS layer that is a service becomes a reference ALIGN reads live (TCK-2207).

ALIGN keeps such a layer the way AGOL does: the address and what names the
layer, never a copy of the data. This module reads what QGIS holds (the
provider, its decoded source) and says what to register, or why nothing can
be. Pure given the decoded source; proven by ``scripts/smoke_services.py``.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from ..i18n import tr

#: Providers whose features live on a remote service.
VECTOR_SERVICE_PROVIDERS = ("wfs", "oapif", "arcgisfeatureserver")

#: Raster providers that are a connection the Map builder can read live.
RASTER_SERVICE_PROVIDERS = ("wms", "arcgismapserver")

#: Point cloud providers (a URL source only).
POINT_CLOUD_PROVIDERS = ("pdal", "ept", "copc", "vpc")

#: Tiled scene providers.
SCENE_PROVIDERS = ("cesiumtiles",)

_HTTP = ("http://", "https://")


class ServicePlan(object):
    """What to register, or why nothing can be.

    :param body: the reference request (``kind``, ``url`` and the kind's fields)
    :param reason: why the layer cannot be a reference, or ``None``
    :param notes: what the briefing says about this reference
    """

    def __init__(self, body=None, reason=None, notes=None):
        self.body = body
        self.reason = reason
        self.notes = notes or []

    @property
    def publishable(self):
        return self.reason is None

    @property
    def kind(self):
        return (self.body or {}).get("kind")


def describe_service(provider, decoded, source="", source_layers=None):
    """Decide from the provider name and QGIS's decoded source. Pure.

    :param source_layers: the tile layers the QGIS style paints (vector tiles)
    """
    name = (provider or "").lower()
    if name == "wms":
        return _wms(decoded)
    if name == "wfs":
        return _wfs(decoded)
    if name == "oapif":
        return _ogc_features(decoded)
    if name == "arcgisfeatureserver":
        return _arcgis_feature(decoded)
    if name == "arcgismapserver":
        return _arcgis_map(decoded)
    if name == "vectortile":
        return _vector_tiles(decoded, source_layers or [])
    if name in POINT_CLOUD_PROVIDERS:
        return _by_url("lidar", decoded, source, tr("ALIGN takes a point cloud by URL only (COPC, EPT); a local file cannot be published."))
    if name in SCENE_PROVIDERS:
        return _by_url("3d-tiles", decoded, source, tr("ALIGN takes a 3D scene by URL only (a 3D Tiles tileset); a local file cannot be published."))
    if name == "gdal":
        return _remote_raster(source)
    return ServicePlan(reason=tr("A {} layer is not a service ALIGN takes from QGIS.").format(provider or "this"))


def is_service_provider(provider):
    name = (provider or "").lower()
    return name in VECTOR_SERVICE_PROVIDERS or name in RASTER_SERVICE_PROVIDERS or name == "vectortile" or name in POINT_CLOUD_PROVIDERS or name in SCENE_PROVIDERS


def remote_cog_url(source):
    """The https address of a COG a GDAL raster reads through `/vsicurl/`, or None."""
    text = (source or "").split("|")[0].strip()
    if text.lower().startswith("/vsicurl/"):
        text = text[len("/vsicurl/"):]
    if text.lower().startswith(_HTTP) and text.lower().split("?")[0].endswith((".tif", ".tiff")):
        return text
    return None


# -- per provider --------------------------------------------------------------

def _wms(decoded):
    url = _text(decoded.get("url"))
    if not url:
        return ServicePlan(reason=tr("The service address is missing from the layer source."))
    kind = _text(decoded.get("type")).lower()
    if kind == "xyz":
        body = {"kind": "xyz", "url": url}
        _zooms(body, decoded)
        return ServicePlan(body, notes=[tr("Published as a live reference to the tile service; no tiles are copied.")])
    layers = _list(decoded.get("layers"))
    styles = _list(decoded.get("styles"))
    image_format = _text(decoded.get("format")) or "image/png"
    matrix_set = _text(decoded.get("tileMatrixSet"))
    if matrix_set:
        if not layers:
            return ServicePlan(reason=tr("The WMTS layer name is missing from the layer source."))
        body = {"kind": "wmts", "url": url, "layer": layers[0], "tileMatrixSet": matrix_set, "imageFormat": image_format}
        if styles and styles[0]:
            body["style"] = styles[0]
        _zooms(body, decoded)
        return ServicePlan(body, notes=[tr("Published as a live reference to the WMTS service (Web Mercator tile matrix sets only); no tiles are copied.")])
    if not layers:
        return ServicePlan(reason=tr("The WMS layer names are missing from the layer source."))
    body = {"kind": "wms", "url": url, "layers": layers, "imageFormat": image_format}
    if any(styles):
        body["styles"] = (styles + [""] * len(layers))[: len(layers)]
    version = _text(decoded.get("version"))
    if version:
        body["version"] = version
    return ServicePlan(body, notes=[tr("Published as a live reference to the WMS service, drawn in Web Mercator; no images are copied.")])


def _wfs(decoded):
    url = _text(decoded.get("url"))
    type_name = _text(decoded.get("typename")) or _text(decoded.get("typeName"))
    if not url or not type_name:
        return ServicePlan(reason=tr("The WFS address or feature type is missing from the layer source."))
    body = {"kind": "wfs", "url": url, "typeName": type_name}
    for key, field in (("version", "version"), ("srsname", "srsName"), ("outputformat", "outputFormat")):
        value = _text(decoded.get(key)) or _text(decoded.get(field))
        if value:
            body[field] = value
    limit = _int(decoded.get("maxNumFeatures"))
    if limit:
        body["maxFeatures"] = limit
    return ServicePlan(body, notes=[tr("Published as a live reference to the WFS service, read when the map opens; no features are copied.")])


def _ogc_features(decoded):
    url = _text(decoded.get("url"))
    collection = _text(decoded.get("typename")) or _text(decoded.get("typeName")) or _text(decoded.get("collection"))
    if not url or not collection:
        return ServicePlan(reason=tr("The OGC API Features address or collection is missing from the layer source."))
    body = {"kind": "ogc-features", "url": url, "collectionId": collection}
    limit = _int(decoded.get("maxNumFeatures"))
    if limit:
        body["maxFeatures"] = limit
    return ServicePlan(body, notes=[tr("Published as a live reference to the OGC API Features collection; no features are copied.")])


def _arcgis_feature(decoded):
    url = _text(decoded.get("url"))
    if not url:
        return ServicePlan(reason=tr("The Feature Service address is missing from the layer source."))
    layer = _text(decoded.get("layer"))
    if layer and not url.rstrip("/").split("/")[-1].isdigit():
        url = url.rstrip("/") + "/" + layer
    return ServicePlan({"kind": "arcgis-feature", "url": url}, notes=[tr("Published as a live reference to the ArcGIS Feature Service; no features are copied.")])


def _arcgis_map(decoded):
    url = _text(decoded.get("url"))
    if not url:
        return ServicePlan(reason=tr("The Map Service address is missing from the layer source."))
    # The Map builder draws a cached Map Service through its tile endpoint.
    template = url.rstrip("/") + "/tile/{z}/{y}/{x}"
    return ServicePlan({"kind": "xyz", "url": template}, notes=[tr("Published as a live reference to the ArcGIS Map Service's tiles (the service must be cached); no images are copied.")])


def _vector_tiles(decoded, source_layers):
    kind = _text(decoded.get("type")).lower()
    url = _text(decoded.get("url")) or _text(decoded.get("path"))
    if kind in ("mbtiles", "pmtiles") and not url.lower().startswith(_HTTP):
        return ServicePlan(reason=tr("A local archive is published as a file, not a reference."))
    if not url.lower().startswith(_HTTP):
        return ServicePlan(reason=tr("The vector tile service address is missing from the layer source."))
    if kind == "pmtiles":
        return ServicePlan({"kind": "pmtiles", "url": url}, notes=[tr("Published as a live reference to the remote PMTiles archive; nothing is copied.")])
    service_type = _text(decoded.get("serviceType")).lower()
    if service_type == "arcgis" or "/vectortileserver" in url.lower():
        root = url.split("/tile/")[0] if "/tile/" in url.lower() else url
        return ServicePlan({"kind": "arcgis-vector-tile", "url": root}, notes=[tr("Published as a live reference to the ArcGIS Vector Tile Service; no tiles are copied.")])
    if not source_layers:
        return ServicePlan(reason=tr("No tile layer is styled in QGIS: the reference cannot say what to draw."))
    body = {"kind": "ogc-vector-tiles", "url": url, "sourceLayers": list(source_layers)}
    _zooms(body, decoded)
    return ServicePlan(body, notes=[tr("Published as a live reference to the vector tile service, drawing the tile layers QGIS styles; no tiles are copied.")])


def _by_url(kind, decoded, source, local_reason):
    url = _text(decoded.get("url")) or _text(decoded.get("path")) or (source or "").strip()
    if not url.lower().startswith(_HTTP):
        return ServicePlan(reason=local_reason)
    note = tr("Published as a live reference to the point cloud; nothing is copied.") if kind == "lidar" else tr("Published as a live reference to the 3D Tiles tileset; nothing is copied.")
    return ServicePlan({"kind": kind, "url": url}, notes=[note])


def _remote_raster(source):
    url = remote_cog_url(source)
    if not url:
        return ServicePlan(reason=tr("A GDAL raster is published as a file."))
    return ServicePlan({"kind": "cog", "url": url}, notes=[tr("Published as a live reference to the remote Cloud-Optimized GeoTIFF; no pixels are copied. The file must be a COG.")])


# -- helpers --------------------------------------------------------------------

def _zooms(body, decoded):
    for key, field in (("zmin", "minzoom"), ("zmax", "maxzoom")):
        value = _int(decoded.get(key))
        if value is not None:
            body[field] = value


def _text(value):
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value).strip() if value is not None else ""


def _list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value]
    text = str(value)
    return [part.strip() for part in text.split(",")] if text != "" else []


def _int(value):
    try:
        return int(str(_text(value)))
    except (TypeError, ValueError):
        return None
