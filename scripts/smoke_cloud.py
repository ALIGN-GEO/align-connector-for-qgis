#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove that a file the Map builder reads as it is leaves as it is (TCK-2206).

    python scripts/smoke_cloud.py

Runs on a plain interpreter — no QGIS. What is under test:

* a FlatGeobuf or GeoParquet already in WGS 84 is sent untouched; any other
  system, a selection, a database table or a GeoJSON goes through the
  export as before;
* a vector tile layer backed by a PMTiles or an MBTiles on disk is an
  archive to send as it is; a remote source or a package is refused with
  the reason; the tile layers QGIS paints are what the web map is told;
* an MBTiles raster is sent as it is and said to become a PMTiles that the
  Map builder does not paint yet; the web map plan leaves it out with that
  reason and places an archive with its tile layers.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import os
import pathlib
import sys
import tempfile
import types

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import qgis_stubs  # noqa: E402

qgis_stubs.install()

from qgis.core import QgsRasterLayer, QgsVectorTileLayer  # noqa: E402

from align_connector.panel import batch, outcome, webmap  # noqa: E402
from align_connector.publish import briefing, raster, support, tiles  # noqa: E402
from align_connector.publish.exporter import vector_passthrough  # noqa: E402


def check(condition, message):
    if not condition:
        raise AssertionError(message)


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


class Archive(QgsVectorTileLayer):
    """A vector tile layer stand-in, styled on some tile layers."""

    def __init__(self, source, name="Tiles", styled=("roads", "buildings"), labelled=("roads", "places")):
        self._source = source
        self._name = name
        self._styled = styled
        self._labelled = labelled

    def id(self):
        return "tiles-1"

    def name(self):
        return self._name

    def isValid(self):  # noqa: N802
        return True

    def source(self):
        return self._source

    def renderer(self):
        return Stub(styles=[Stub(layerName=n) for n in self._styled])

    def labeling(self):
        return Stub(styles=[Stub(layerName=n) for n in self._labelled])

    def metadata(self):
        return None


class Raster(QgsRasterLayer):
    def __init__(self, source, name="Tiles raster"):
        self._source = source
        self._name = name

    def id(self):
        return "r-1"

    def name(self):
        return self._name

    def isValid(self):  # noqa: N802
        return True

    def source(self):
        return self._source

    def dataProvider(self):  # noqa: N802
        return Stub(name="gdal")

    def metadata(self):
        return None


def _temp(suffix):
    handle, path = tempfile.mkstemp(suffix=suffix)
    os.write(handle, b"\0" * 4096)
    os.close(handle)
    return path


# -- vector files ---------------------------------------------------------------

def test_a_cloud_native_vector_file_in_wgs84_leaves_as_it_is():
    check(vector_passthrough("C:/data/parcels.fgb", "ogr", "EPSG:4326") == ("C:/data/parcels.fgb", "fgb", "application/x-flatgeobuf"), "fgb")
    check(vector_passthrough("C:/data/parcels.parquet|layername=parcels", "ogr", "OGC:CRS84") == ("C:/data/parcels.parquet", "parquet", "application/vnd.apache.parquet"), "parquet, OGR options stripped")
    check(vector_passthrough("C:/data/parcels.geoparquet", "OGR", "EPSG:4326")[1] == "parquet", "geoparquet")
    check(vector_passthrough("C:/data/parcels.fgb", "ogr", "EPSG:31370") is None, "Lambert 72 is rewritten in WGS 84")
    check(vector_passthrough("C:/data/parcels.fgb", "ogr", "", valid=False) is None, "no system: the export refuses, not the passthrough")
    check(vector_passthrough("C:/data/parcels.fgb", "ogr", "EPSG:4326", selected_only=True) is None, "a selection is written, not the file")
    check(vector_passthrough("C:/data/parcels.fgb", "memory", "EPSG:4326") is None, "a memory layer has no file")
    check(vector_passthrough("C:/data/parcels.geojson", "ogr", "EPSG:4326") is None, "GeoJSON is rewritten as FlatGeobuf")
    check(vector_passthrough("dbname=x table=parcels", "postgres", "EPSG:4326") is None, "a table is written")


# -- archives ----------------------------------------------------------------------

def test_an_archive_on_disk_is_sent_as_it_is_and_anything_remote_is_named():
    plan = tiles.archive_plan({"type": "mbtiles", "path": "C:/data/tiles.mbtiles"})
    check(plan.publishable and plan.kind == "mbtiles" and plan.path == "C:/data/tiles.mbtiles", "mbtiles by path")
    plan = tiles.archive_plan({"type": "pmtiles", "url": "file:///C:/data/tiles.pmtiles"})
    check(plan.publishable and plan.kind == "pmtiles" and plan.path == "C:/data/tiles.pmtiles", "a file URL reads as a path: " + str(plan.path))
    plan = tiles.archive_plan({"type": "pmtiles", "url": "file:///home/me/tiles.pmtiles"})
    check(plan.path == "/home/me/tiles.pmtiles", plan.path)
    plan = tiles.archive_plan({"type": "pmtiles", "url": "https://tiles.example/world.pmtiles"})
    check(not plan.publishable and "reference" in plan.reason, plan.reason)
    plan = tiles.archive_plan({"type": "xyz", "url": "https://tiles.example/{z}/{x}/{y}.pbf"})
    check(not plan.publishable and "reference" in plan.reason, plan.reason)
    plan = tiles.archive_plan({"type": "vtpk", "path": "C:/data/tiles.vtpk"})
    check(not plan.publishable and "MBTiles" in plan.reason, plan.reason)
    plan = tiles.archive_plan({})
    check(not plan.publishable, "nothing decoded is refused")

    path = _temp(".pmtiles")
    try:
        layer = Archive("type=pmtiles&url={}".format(path))
        check(tiles.source_layers_of(layer) == ["roads", "buildings", "places"], tiles.source_layers_of(layer))
        verdict = support.verdict(layer)
        check(verdict.kind == "archive" and verdict.publishable and verdict.unplaceable is None, verdict.reason)
        check(any("sent as it is" in n for n in verdict.notes), verdict.notes)
        info = briefing.brief(layer)
        check(info.text.startswith("The PMTILES archive will be sent as it is (4.0 KB)."), info.text)
        check("Tile layers for the web map: roads, buildings, places." in info.text, info.text)
        rows = batch.rows_of([layer])
        check(rows[0].publishable and rows[0].kind == "archive", rows[0].reason)
        export = tiles.export_archive(layer)
        check(export.path == path and export.filename == "Tiles.pmtiles" and export.workdir is None and export.reused_source, "the archive itself leaves")
        check(export.content_type == "application/octet-stream", export.content_type)

        bare = Archive("type=mbtiles&path={}".format(path), styled=(), labelled=())
        verdict = support.verdict(bare)
        check(verdict.publishable and verdict.unplaceable and "no tile layer is styled" in verdict.unplaceable, verdict.unplaceable)
    finally:
        os.remove(path)

    missing = Archive("type=mbtiles&path=C:/nowhere/tiles.mbtiles")
    verdict = support.verdict(missing)
    check(not verdict.publishable and "missing on disk" in verdict.reason, verdict.reason)


def test_an_mbtiles_raster_is_sent_as_it_is_and_kept_off_the_web_map():
    path = _temp(".mbtiles")
    try:
        layer = Raster(path)
        verdict = support.verdict(layer)
        check(verdict.kind == "raster" and verdict.publishable, verdict.reason)
        check(any("PMTiles" in n for n in verdict.notes), verdict.notes)
        check(verdict.unplaceable == "raster tiles are not displayed by the Map builder yet", verdict.unplaceable)
        info = briefing.brief(layer)
        check(info.text.startswith("The MBTiles archive will be sent as it is (4.0 KB)."), info.text)
        export = raster.export_raster(layer)
        check(export.path == path and export.filename == "Tiles-raster.mbtiles" and export.content_type == "application/octet-stream", export.filename)
        check(export.already_servable is False and export.workdir is None, "an archive is never declared a COG")
    finally:
        os.remove(path)


# -- the web map and the outcome ------------------------------------------------------

def _request(title, visible=True, geometry=None, source_layers=None, unplaceable=None):
    return types.SimpleNamespace(
        plan=types.SimpleNamespace(title=title), visible=visible, geometry_type=geometry,
        source_layers=source_layers or [], unplaceable=unplaceable, briefing=types.SimpleNamespace(style=None),
    )


def _task(asset_id, servable=None, gis_format=None):
    return types.SimpleNamespace(asset_id=asset_id, servable_asset_id=servable, derivation_error=None, gis_format=gis_format,
                                 asset_name="Tiles", org_name="Town GIS", flattened_fields=[], locked_fields=[], was_cancelled=False, error_message=None)


def test_the_web_map_places_an_archive_with_its_tile_layers_and_names_what_it_cannot():
    records = [
        (_request("Tiles", source_layers=["roads", "buildings"]), _task("up-1", servable="pm-1", gis_format="pmtiles"), True),
        (_request("Ortho tiles", unplaceable="raster tiles are not displayed by the Map builder yet"), _task("up-2", servable="pm-2", gis_format="pmtiles"), True),
    ]
    plan = webmap.plan(records)
    check(plan.layers == [{"assetId": "pm-1", "title": "Tiles", "visible": True, "sourceLayers": ["roads", "buildings"]}], plan.layers)
    check(plan.left_out == [("Ortho tiles", "raster tiles are not displayed by the Map builder yet")], plan.left_out)

    done = types.SimpleNamespace(name="Site", org_name="Town GIS", placed=[{"assetId": "pm-1"}], skipped=[], error_message=None)
    text = webmap.describe_outcome(done, True, plan.left_out)
    check(text.endswith("Not placed: Ortho tiles (raster tiles are not displayed by the Map builder yet)."), text)


def test_the_outcome_says_an_mbtiles_became_a_pmtiles():
    converted = outcome.outcome_of(_task("up-1", servable="pm-1", gis_format="pmtiles"), True)
    check("converted to PMTiles" in converted.text and "reprojected" not in converted.text, converted.text)
    check(converted.asset_id == "pm-1", "the link opens the PMTiles")
    untouched = outcome.outcome_of(_task("up-3", gis_format="pmtiles"), True)
    check(untouched.text == "\u201cTiles\u201d is now in Town GIS on ALIGN.", untouched.text)


def main():
    tests = [
        test_a_cloud_native_vector_file_in_wgs84_leaves_as_it_is,
        test_an_archive_on_disk_is_sent_as_it_is_and_anything_remote_is_named,
        test_an_mbtiles_raster_is_sent_as_it_is_and_kept_off_the_web_map,
        test_the_web_map_places_an_archive_with_its_tile_layers_and_names_what_it_cannot,
        test_the_outcome_says_an_mbtiles_became_a_pmtiles,
    ]
    for test in tests:
        test()
        print("ok  {}".format(test.__name__))
    print("{} checks passed".format(len(tests)))


if __name__ == "__main__":
    main()
