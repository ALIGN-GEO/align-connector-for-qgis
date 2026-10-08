#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove that a layer that is a service becomes a live reference (TCK-2207).

    python scripts/smoke_services.py

Runs on a plain interpreter — no QGIS, no network. What is under test:

* the two dialects of a QGIS source (a query string, a data-source URI)
  read into the same dict;
* per provider, what QGIS holds becomes the request ALIGN registers: XYZ,
  WMTS and WMS from the wms provider; WFS, OGC API Features and ArcGIS
  Feature Service; a cached ArcGIS Map Service as tiles; remote vector
  tiles, a remote PMTiles, an ArcGIS Vector Tile Service; a point cloud and
  a 3D Tiles tileset by URL; a COG read through /vsicurl/;
* what cannot be a reference says why (a local point cloud, unstyled vector
  tiles, a missing address);
* the verdict, the briefing and the outcome for such a layer, and the
  request that travels.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import pathlib
import sys
import types

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import qgis_stubs  # noqa: E402

qgis_stubs.install()

from qgis.core import QgsPointCloudLayer, QgsRasterLayer, QgsVectorTileLayer  # noqa: E402

from align_connector import api, config, net  # noqa: E402
from align_connector.panel import outcome  # noqa: E402
from align_connector.publish import briefing, services, support, uri  # noqa: E402


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def plan(provider, source, source_layers=None):
    return services.describe_service(provider, uri.parse(source), source, source_layers)


# -- reading a source ------------------------------------------------------------

def test_both_source_dialects_read_the_same_way():
    query = uri.parse("type=xyz&url=https://tile.example/%7Bz%7D/%7Bx%7D/%7By%7D.png&zmax=19&zmin=0")
    check(query == {"type": "xyz", "url": "https://tile.example/{z}/{x}/{y}.png", "zmax": "19", "zmin": "0"}, query)
    wms = uri.parse("contextualWMSLegend=0&crs=EPSG:3857&format=image/png&layers=roads&layers=rivers&styles=&styles=blue&url=https://wms.example/ows")
    check(wms["layers"] == ["roads", "rivers"] and wms["styles"] == ["", "blue"], wms)
    datasource = uri.parse(" url='https://wfs.example/ows?map=x' typename='ns:parcels' version='2.0.0' srsname='EPSG:4326' restrictToRequestBBOX='1' ")
    check(datasource == {"url": "https://wfs.example/ows?map=x", "typename": "ns:parcels", "version": "2.0.0", "srsname": "EPSG:4326", "restrictToRequestBBOX": "1"}, datasource)
    check(uri.parse("") == {}, "nothing reads as nothing")


# -- per provider ----------------------------------------------------------------

def test_tile_services_become_tile_references():
    xyz = plan("wms", "type=xyz&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png&zmax=19&zmin=0")
    check(xyz.publishable and xyz.body == {"kind": "xyz", "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png", "minzoom": 0, "maxzoom": 19}, xyz.body)
    check(any("live reference" in n for n in xyz.notes), xyz.notes)

    wmts = plan("wms", "format=image/jpeg&layers=ortho&styles=default&tileMatrixSet=GoogleMapsCompatible&url=https://wmts.example/service?SERVICE=WMTS%26REQUEST=GetCapabilities")
    check(wmts.body == {"kind": "wmts", "url": "https://wmts.example/service?SERVICE=WMTS&REQUEST=GetCapabilities", "layer": "ortho", "tileMatrixSet": "GoogleMapsCompatible", "imageFormat": "image/jpeg", "style": "default"}, wmts.body)

    wms = plan("wms", "crs=EPSG:3857&format=image/png&layers=roads&layers=rivers&styles=&styles=blue&url=https://wms.example/ows")
    check(wms.body == {"kind": "wms", "url": "https://wms.example/ows", "layers": ["roads", "rivers"], "imageFormat": "image/png", "styles": ["", "blue"]}, wms.body)
    one = plan("wms", "format=image/png&layers=roads&styles=&url=https://wms.example/ows")
    check("styles" not in one.body, "no named style, no styles field")
    check(not plan("wms", "format=image/png&url=https://wms.example/ows").publishable, "a WMS without layers is refused")
    check("missing" in plan("wms", "layers=roads").reason, "no address, no reference")

    arcgis_map = plan("arcgismapserver", "url='https://gis.example/arcgis/rest/services/Base/MapServer' crs='EPSG:3857'")
    check(arcgis_map.body == {"kind": "xyz", "url": "https://gis.example/arcgis/rest/services/Base/MapServer/tile/{z}/{y}/{x}"}, arcgis_map.body)


def test_feature_services_become_live_references():
    wfs = plan("WFS", "url='https://wfs.example/ows' typename='ns:parcels' version='2.0.0' srsname='EPSG:4326' maxNumFeatures='5000'")
    check(wfs.body == {"kind": "wfs", "url": "https://wfs.example/ows", "typeName": "ns:parcels", "version": "2.0.0", "srsName": "EPSG:4326", "maxFeatures": 5000}, wfs.body)
    check("missing" in plan("WFS", "url='https://wfs.example/ows'").reason, "a WFS needs its type")

    features = plan("OAPIF", "url='https://api.example/ogc' typename='parcels'")
    check(features.body == {"kind": "ogc-features", "url": "https://api.example/ogc", "collectionId": "parcels"}, features.body)

    feature = plan("arcgisfeatureserver", "url='https://gis.example/arcgis/rest/services/Parcels/FeatureServer/3' crs='EPSG:4326'")
    check(feature.body == {"kind": "arcgis-feature", "url": "https://gis.example/arcgis/rest/services/Parcels/FeatureServer/3"}, feature.body)
    layered = plan("arcgisfeatureserver", "url='https://gis.example/arcgis/rest/services/Parcels/FeatureServer' layer='3'")
    check(layered.body["url"].endswith("/FeatureServer/3"), layered.body)


def test_vector_tiles_point_clouds_scenes_and_remote_cogs():
    tiles = plan("vectortile", "type=xyz&url=https://tiles.example/{z}/{x}/{y}.pbf&zmax=14&zmin=4", ["roads", "water"])
    check(tiles.body == {"kind": "ogc-vector-tiles", "url": "https://tiles.example/{z}/{x}/{y}.pbf", "sourceLayers": ["roads", "water"], "minzoom": 4, "maxzoom": 14}, tiles.body)
    check("styled" in plan("vectortile", "type=xyz&url=https://tiles.example/{z}/{x}/{y}.pbf").reason, "unstyled tiles cannot say what to draw")
    remote = plan("vectortile", "type=pmtiles&url=https://tiles.example/world.pmtiles")
    check(remote.body == {"kind": "pmtiles", "url": "https://tiles.example/world.pmtiles"}, remote.body)
    esri = plan("vectortile", "type=xyz&serviceType=arcgis&url=https://gis.example/arcgis/rest/services/Base/VectorTileServer/tile/{z}/{y}/{x}.pbf")
    check(esri.body == {"kind": "arcgis-vector-tile", "url": "https://gis.example/arcgis/rest/services/Base/VectorTileServer"}, esri.body)
    check("file" in plan("vectortile", "type=mbtiles&path=C:/data/tiles.mbtiles").reason, "a local archive is a file, not a reference")

    cloud = plan("copc", "https://data.example/cloud.copc.laz")
    check(cloud.body == {"kind": "lidar", "url": "https://data.example/cloud.copc.laz"}, cloud.body)
    check("URL only" in plan("pdal", "C:/data/cloud.laz").reason, "a local point cloud has no path to ALIGN")
    scene = plan("cesiumtiles", "url=https://data.example/tileset.json")
    check(scene.body == {"kind": "3d-tiles", "url": "https://data.example/tileset.json"}, scene.body)

    cog = plan("gdal", "/vsicurl/https://data.example/dem.tif")
    check(cog.body == {"kind": "cog", "url": "https://data.example/dem.tif"}, cog.body)
    check(services.remote_cog_url("C:/data/dem.tif") is None and services.remote_cog_url("/vsicurl/https://x.example/a.nc") is None, "only a remote GeoTIFF")
    check(not plan("mesh", "x").publishable, "an unknown provider is not a service")


# -- the layer, the briefing, the outcome ------------------------------------------------

class Stub(object):
    def __init__(self, **attrs):
        self._attrs = attrs

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._attrs:
            value = self._attrs[name]
            return lambda *args: value
        raise AttributeError(name)


class Tiles(QgsVectorTileLayer):
    def __init__(self, source, styled=("roads",)):
        self._source = source
        self._styled = styled

    def id(self):
        return "t"

    def name(self):
        return "Tiles"

    def isValid(self):  # noqa: N802
        return True

    def source(self):
        return self._source

    def renderer(self):
        return Stub(styles=[Stub(layerName=n) for n in self._styled])

    def labeling(self):
        return None

    def metadata(self):
        return None


class Raster(QgsRasterLayer):
    def __init__(self, source, provider="wms"):
        self._source = source
        self._provider = provider

    def id(self):
        return "r"

    def name(self):
        return "Ortho"

    def isValid(self):  # noqa: N802
        return True

    def source(self):
        return self._source

    def dataProvider(self):  # noqa: N802
        return Stub(name=self._provider)

    def metadata(self):
        return None


class QgsPointCloudLayerStub(QgsPointCloudLayer):
    def __init__(self, source):
        self._source = source

    def isValid(self):  # noqa: N802
        return True

    def providerType(self):  # noqa: N802
        return "copc"

    def source(self):
        return self._source

    def name(self):
        return "Cloud"

    def metadata(self):
        return None


QgsPointCloudLayerStub.__name__ = "QgsPointCloudLayer"


def test_the_verdict_and_the_briefing_say_a_reference():
    remote = Tiles("type=xyz&url=https://tiles.example/{z}/{x}/{y}.pbf&zmin=4&zmax=14", styled=("roads", "water"))
    verdict = support.verdict(remote)
    check(verdict.kind == "reference" and verdict.publishable and verdict.reference["kind"] == "ogc-vector-tiles", verdict.reason)
    check(verdict.reference["sourceLayers"] == ["roads", "water"], verdict.reference)
    info = briefing.brief(remote)
    check(info.kind == "reference" and info.reference == verdict.reference and "reads the service when the map opens" in info.text, info.text)
    check(info.style is None and info.iso is None, "nothing but the address travels")

    ortho = support.verdict(Raster("tileMatrixSet=GoogleMapsCompatible&layers=ortho&styles=&format=image/png&url=https://wmts.example/service"))
    check(ortho.kind == "reference" and ortho.reference["kind"] == "wmts", ortho.reason)
    cog = support.verdict(Raster("/vsicurl/https://data.example/dem.tif", provider="gdal"))
    check(cog.kind == "reference" and cog.reference["kind"] == "cog", cog.reason)
    coverage = support.verdict(Raster("url=https://wcs.example&identifier=dem", provider="wcs"))
    check(not coverage.publishable and coverage.reason.startswith("A WCS connection"), coverage.reason)

    cloud = support.verdict(QgsPointCloudLayerStub("https://data.example/cloud.copc.laz"))
    check(cloud.kind == "reference" and cloud.reference == {"kind": "lidar", "url": "https://data.example/cloud.copc.laz"}, cloud.reason)
    local = support.verdict(QgsPointCloudLayerStub("C:/data/cloud.laz"))
    check(not local.publishable and "URL only" in local.reason, local.reason)


class _Session(object):
    def access_token(self):
        return "alg_at_test"


def test_the_request_travels_and_the_outcome_reads_created_or_reused():
    calls = []

    def post_json(url, payload, bearer=None):
        calls.append((url, payload, bearer))
        return net.HttpResult(200, {"assetId": "ref-1", "created": False, "name": "OSM", "gisFormat": "xyz"})

    api.net.post_json = post_json
    answer = api.create_reference(_Session(), "org-1", "OSM", {"kind": "xyz", "url": "https://t.example/{z}/{x}/{y}.png"},
                                  provenance={"source": "qgis-plugin"}, description="Base", visibility="org")
    url, payload, bearer = calls[0]
    check(url == config.references_endpoint("org-1") and url.endswith("/api/v1/orgs/org-1/datastore/references"), url)
    check(payload == {"kind": "xyz", "url": "https://t.example/{z}/{x}/{y}.png", "name": "OSM", "provenance": {"source": "qgis-plugin"}, "description": "Base", "visibility": "org"}, payload)
    check(bearer == "alg_at_test" and answer == {"assetId": "ref-1", "created": False, "name": "OSM", "gisFormat": "xyz"}, answer)

    reused = types.SimpleNamespace(asset_name="OSM", org_name="Town GIS", asset_id="ref-1", created=False, derivation_error=None,
                                   flattened_fields=[], servable_asset_id=None, gis_format="xyz", locked_fields=[], was_cancelled=False, error_message=None)
    text = outcome.outcome_of(reused, True)
    check(text.text == "\u201cOSM\u201d was already in Town GIS on ALIGN; the existing reference is reused." and text.asset_id == "ref-1", text.text)
    created = outcome.outcome_of(types.SimpleNamespace(**dict(vars(reused), created=True)), True)
    check(created.text == "\u201cOSM\u201d is now in Town GIS on ALIGN. It is a live reference: ALIGN reads the service when the map opens.", created.text)


def main():
    tests = [
        test_both_source_dialects_read_the_same_way,
        test_tile_services_become_tile_references,
        test_feature_services_become_live_references,
        test_vector_tiles_point_clouds_scenes_and_remote_cogs,
        test_the_verdict_and_the_briefing_say_a_reference,
        test_the_request_travels_and_the_outcome_reads_created_or_reused,
    ]
    for test in tests:
        test()
        print("ok  {}".format(test.__name__))
    print("{} checks passed".format(len(tests)))


if __name__ == "__main__":
    main()
