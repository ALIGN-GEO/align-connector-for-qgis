#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove what the ALIGN panel decides, without a single widget.

    python scripts/smoke_panel.py

Runs on a plain interpreter — no QGIS. Layers are stand-ins answering the
QGIS method names; what is under test are the decisions behind the panel
(TCK-2198):

* which layers are listed: every one, in the order given, with a reason for
  each one the plugin cannot send (never a silent absence);
* what one click sends: the checked, publishable rows, in panel order, each
  under its own title (renamed, or the record's own, or the layer name), with
  "selection only" honoured for vectors alone;
* what a layer is told before the send: features and reprojection, style and
  metadata for a vector; file size and conversion for a raster;
* what a finished publication is told, success, warning or cancellation, and
  which item the "Open in ALIGN" link points to.

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

from qgis.core import QgsRasterLayer, QgsVectorLayer  # noqa: E402

from align_connector import config  # noqa: E402
from align_connector.panel import batch, outcome  # noqa: E402
from align_connector.publish import briefing, support  # noqa: E402
from align_connector.publish.wording import readable_size  # noqa: E402


class Stub(object):
    """An object whose methods return the values given at construction."""

    def __init__(self, **attrs):
        self._attrs = attrs

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._attrs:
            value = self._attrs[name]
            return lambda *args: value
        raise AttributeError(name)


def _fill_symbol(fill="#1d4ed8"):
    color = Stub(name=fill, alphaF=1.0)
    layer = Stub(
        layerType="SimpleFill", fillColor=color, color=color, strokeColor=Stub(name="#1e3a8a", alphaF=1.0),
        strokeWidth=0.4, strokeWidthUnit="MM", strokeStyle=1, brushStyle=1,
        dataDefinedProperties=Stub(hasActiveProperties=False),
    )
    return Stub(symbolLayers=[layer], color=color, opacity=1.0)


class Vector(QgsVectorLayer):
    """A vector layer stand-in: valid, in Lambert 72, categorized, documented."""

    def __init__(self, layer_id, name, authid="EPSG:31370", valid=True, selected=0, total=120, title=None, renderer=None,
                 geometry=2, wkb="Polygon", provider="ogr"):
        self._id = layer_id
        self._name = name
        self._authid = authid
        self._valid = valid
        self._selected = selected
        self._total = total
        self._geometry = geometry
        self._wkb = wkb
        self._provider = provider
        self._record = Stub(title=title or "", abstract="", keywords={}, licenses=[], rights=[], constraints=[], contacts=[], history=[])
        self._renderer = renderer or Stub(type="singleSymbol", symbol=_fill_symbol())

    def id(self):
        return self._id

    def name(self):
        return self._name

    def isValid(self):  # noqa: N802
        return self._valid

    def crs(self):
        return Stub(authid=self._authid, isValid=True)

    def selectedFeatureCount(self):  # noqa: N802
        return self._selected

    def featureCount(self):  # noqa: N802
        return self._total

    def geometryType(self):  # noqa: N802
        return self._geometry

    def wkbType(self):  # noqa: N802
        return self._wkb

    def dataProvider(self):  # noqa: N802
        return Stub(name=self._provider)

    def renderer(self):
        return self._renderer

    def labelsEnabled(self):  # noqa: N802
        return False

    def labeling(self):
        return None

    def metadata(self):
        return self._record

    def abstract(self):
        return ""


class Raster(QgsRasterLayer):
    """A raster layer stand-in: a file on disk, or a remote service."""

    def __init__(self, layer_id, name, source="", provider="gdal", valid=True):
        self._id = layer_id
        self._name = name
        self._source = source
        self._provider = provider
        self._valid = valid

    def id(self):
        return self._id

    def name(self):
        return self._name

    def isValid(self):  # noqa: N802
        return self._valid

    def source(self):
        return self._source

    def dataProvider(self):  # noqa: N802
        return Stub(name=self._provider)

    def metadata(self):
        return None


class QgsMeshLayer(object):
    """Neither vector nor raster: the class name is what QGIS would answer."""

    def id(self):
        return "mesh-1"

    def name(self):
        return "Bathymetry mesh"

    def isValid(self):  # noqa: N802
        return True


def check(condition, message):
    if not condition:
        raise AssertionError(message)


# -- the list -----------------------------------------------------------------

def test_every_layer_is_listed_with_a_reason_for_the_refused_ones():
    layers = [
        Vector("v1", "Parcels", title="Cadastral parcels 2026"),
        Raster("r1", "Elevation", source="C:/data/dem.tif"),
        Raster("r2", "Orthophoto (WMS)", source="url=https://example", provider="wms"),
        Vector("v2", "Broken", valid=False),
        QgsMeshLayer(),
    ]
    rows = batch.rows_of(layers)
    check([r.layer_id for r in rows] == ["v1", "r1", "r2", "mesh-1"] or [r.name for r in rows] == ["Parcels", "Elevation", "Orthophoto (WMS)", "Broken", "Bathymetry mesh"], "every layer, in the order given")
    check(len(rows) == 5, "none hidden, not even the ones that cannot be sent")
    check(rows[0].publishable and rows[0].kind == "vector" and rows[0].title == "Cadastral parcels 2026", "the record's title is proposed")
    check(rows[1].publishable and rows[1].kind == "raster" and rows[1].title == "Elevation", "a raster proposes its layer name")
    check(not rows[2].publishable and "WMS" in rows[2].reason and "no file" in rows[2].reason, rows[2].reason)
    check(not rows[3].publishable and rows[3].kind is None, "an invalid layer is listed, refused")
    check(not rows[4].publishable and "mesh layer" in rows[4].reason, rows[4].reason)


def test_every_layer_gets_a_verdict_in_words_to_act_on():
    rows = batch.rows_of([
        Vector("table", "Attribute table", geometry=4, wkb="NoGeometry"),
        Vector("arcs", "Road arcs", wkb="CompoundCurve"),
        Vector("empty", "Empty", total=0),
        Vector("wfs", "Parcels (WFS)", provider="WFS"),
        Raster("xyz", "OpenStreetMap", source="type=xyz&url=https://tile.example/{z}/{x}/{y}.png", provider="wms"),
        Raster("wmts", "Ortho (WMTS)", source="tileMatrixSet=EPSG:3857&url=https://wmts.example", provider="wms"),
        Raster("dem", "Elevation", source="C:/data/dem.tif"),
    ])
    reasons = {row.layer_id: row.reason for row in rows}
    check("without geometry" in reasons["table"], reasons["table"])
    check("Curved geometries" in reasons["arcs"] and "Segmentize" in reasons["arcs"], reasons["arcs"])
    check("No features" in reasons["empty"], reasons["empty"])
    check(reasons["wfs"] is None, "a WFS layer is published as a snapshot, not refused")
    check(reasons["xyz"].startswith("A XYZ tiles connection") and "web application" in reasons["xyz"], reasons["xyz"])
    check(reasons["wmts"].startswith("A WMTS connection"), reasons["wmts"])
    check(reasons["dem"] is None, "a GDAL file is published")

    summary = batch.describe_blocked(rows)
    check(summary.startswith("5 layers cannot be published:"), summary)
    check("Attribute table (" in summary and "OpenStreetMap (" in summary, summary)
    check(batch.describe_blocked(rows[3:4]) == "", "nothing blocked reads as nothing")

    # The briefing carries what changes on the way for the layers that go.
    snapshot = briefing.brief(Vector("wfs", "Parcels (WFS)", provider="WFS"))
    check("snapshot of its features" in snapshot.text, snapshot.text)
    refused = briefing.brief(Vector("arcs", "Road arcs", wkb="CompoundCurve"))
    check(refused.kind is None and "Curved geometries" in refused.text, refused.text)
    check(support.geojson_geometry_type(Vector("pts", "Points", geometry=0)) == "Point", "geometry for the web map")
    check(support.geojson_geometry_type(Vector("lines", "Lines", geometry=types.SimpleNamespace(name="Line", value=1))) == "LineString",
          "a QGIS 4 enum geometry reads by name")
    check(support.geojson_geometry_type(Vector("table", "Table", geometry=4)) is None, "no geometry, no type")


def test_one_click_sends_the_checked_publishable_rows_in_panel_order():
    rows = batch.rows_of([
        Vector("v1", "Parcels", title="Cadastral parcels 2026", selected=3),
        Raster("r1", "Elevation", source="C:/data/dem.tif"),
        Raster("r2", "Orthophoto (WMS)", provider="wms"),
        Vector("v2", "Roads"),
    ])
    plans = batch.plan(rows, checked_ids=["v2", "r2", "v1"], titles={"v2": "  Road network  ", "v1": ""}, selected_only_ids=["v1", "r2"])
    check([p.layer_id for p in plans] == ["v1", "v2"], "panel order, refused rows skipped even when checked")
    check(plans[0].title == "Cadastral parcels 2026", "a blank rename falls back to the proposed title")
    check(plans[1].title == "Road network", "a rename is trimmed and kept")
    check(plans[0].selected_only is True and plans[1].selected_only is False, "selection only, per layer")

    none = batch.plan(rows, checked_ids=[])
    check(none == [] and batch.describe_plan(none) == "Nothing selected", batch.describe_plan(none))
    check(batch.describe_plan(plans[:1]) == "Publish 1 layer", batch.describe_plan(plans[:1]))
    check(batch.describe_plan(plans) == "Publish 2 layers", batch.describe_plan(plans))

    raster_only = batch.plan(rows, checked_ids=["r1"], selected_only_ids=["r1"])
    check(raster_only[0].selected_only is False, "a selection means nothing for a raster")


# -- the briefing -------------------------------------------------------------

def test_a_vector_is_told_its_features_reprojection_style_and_metadata():
    categories = [
        Stub(value="A", label="Zone A", symbol=_fill_symbol("#f59e0b"), renderState=True),
        Stub(value="B", label="Zone B", symbol=_fill_symbol("#10b981"), renderState=True),
    ]
    renderer = Stub(type="categorizedSymbol", classAttribute="zone", categories=categories)
    layer = Vector("v1", "Parcels", title="Cadastral parcels 2026", selected=3, total=120, renderer=renderer)

    info = briefing.brief(layer)
    check(info.kind == "vector" and info.title == "Cadastral parcels 2026", info.title)
    check(info.text.startswith("120 features will be written"), info.text)
    check("31370" in info.text, "the reprojection is announced before the send: " + info.text)
    check("Style: categorized on zone, 2 classes." in info.text, info.text)
    check(info.style is not None and info.style["symbology"]["attribute"] == "zone", "the converted style travels with the briefing")
    check(info.iso is None, "a record with only a title has nothing for the sheet")

    only_selected = briefing.brief(layer, selected_only=True)
    check(only_selected.text.startswith("3 features will be written"), only_selected.text)

    wgs84 = briefing.brief(Vector("v3", "Points", authid="EPSG:4326"))
    check("4326" not in wgs84.text.split("Style:")[0] or "already" in wgs84.text, "a layer already in WGS 84 is not told it will be converted: " + wgs84.text)


def test_a_raster_is_told_its_size_and_the_conversion_on_arrival():
    handle, path = tempfile.mkstemp(suffix=".tif")
    try:
        os.write(handle, b"\0" * 2048)
        os.close(handle)
        info = briefing.brief(Raster("r1", "Elevation", source=path))
        check(info.kind == "raster" and info.style is None and info.title == "Elevation", info.title)
        check("sent as it is (2.0 KB)" in info.text and "Cloud-Optimized GeoTIFF" in info.text, info.text)
    finally:
        os.remove(path)

    written = briefing.brief(Raster("r2", "Derived", source="C:/nowhere/derived.vrt"))
    check(written.text.startswith("A compressed GeoTIFF will be written"), written.text)

    neither = briefing.brief(QgsMeshLayer())
    check(neither.kind is None and "mesh layer" in neither.text, neither.text)
    styled = briefing.brief(Raster("r3", "Hillshade", source="C:/nowhere/hill.tif"))
    check("Raster styling is not transmitted" in styled.text, styled.text)


# -- the outcome --------------------------------------------------------------

def _task(**attrs):
    base = dict(
        asset_name="Parcels", org_name="Town GIS", asset_id="asset-1", derivation_error=None,
        flattened_fields=[], servable_asset_id=None, gis_format="fgb", locked_fields=[],
        was_cancelled=False, error_message=None,
    )
    base.update(attrs)
    return types.SimpleNamespace(**base)


def test_a_success_says_where_the_layer_is_and_what_changed():
    plain = outcome.outcome_of(_task(), True)
    check(plain.level == outcome.SUCCESS and plain.text == "\u201cParcels\u201d is now in Town GIS on ALIGN.", plain.text)
    check(plain.asset_id == "asset-1", "the link opens the item just created")

    cog = outcome.outcome_of(_task(gis_format="cog"), True)
    check(cog.text.endswith("It was converted to a Cloud-Optimized GeoTIFF."), cog.text)

    copied = outcome.outcome_of(_task(gis_format="cog", servable_asset_id="asset-2"), True)
    check("your original was kept" in copied.text and "both use storage" in copied.text, copied.text)
    check(copied.asset_id == "asset-2", "the link opens the copy that displays, not the original")

    detailed = outcome.outcome_of(_task(flattened_fields=["tags", "props"], locked_fields=["licence", "contact"]), True)
    check("Complex fields were stored as text: tags, props." in detailed.text, detailed.text)
    check("The licence, contact on the item's sheet are set by your organization" in detailed.text, detailed.text)


def test_a_stored_but_undisplayable_file_is_a_warning_not_a_success():
    stored = outcome.outcome_of(_task(derivation_error="GDAL could not read band 4."), True)
    check(stored.level == outcome.WARNING, "no celebration for a file nothing can display")
    check("could not prepare it for display: GDAL could not read band 4." in stored.text, stored.text)
    check(stored.asset_id == "asset-1", "the item still exists; the link still helps")


def test_a_failure_speaks_in_the_refusers_words_and_a_cancellation_is_quiet():
    refused = outcome.outcome_of(_task(error_message="Your role in Town GIS does not allow publishing."), False)
    check(refused.level == outcome.WARNING and refused.text == "Your role in Town GIS does not allow publishing.", refused.text)
    check(refused.asset_id is None, "nothing to open after a refusal")

    silent = outcome.outcome_of(_task(), False)
    check(silent.text == "Publishing failed.", silent.text)

    cancelled = outcome.outcome_of(_task(was_cancelled=True), False)
    check(cancelled.level == outcome.INFO and cancelled.text == "Publishing cancelled. Nothing was catalogued.", cancelled.text)


# -- the link and the sizes ---------------------------------------------------

def test_the_item_link_is_the_web_applications_item_page():
    url = config.item_url("org-1", "asset-9")
    check(url.endswith("/app/org-1/datastore/asset-9"), url)
    check(url.startswith("http"), url)


def test_sizes_read_at_a_glance():
    check(readable_size(None) is None and readable_size(0) is None, "unknown stays unknown")
    check(readable_size(500) == "500 bytes", readable_size(500))
    check(readable_size(19.2 * 1024 * 1024) == "19.2 MB", readable_size(19.2 * 1024 * 1024))
    check(readable_size(3 * 1024 ** 3) == "3.0 GB", readable_size(3 * 1024 ** 3))


def main():
    tests = [
        test_every_layer_is_listed_with_a_reason_for_the_refused_ones,
        test_one_click_sends_the_checked_publishable_rows_in_panel_order,
        test_every_layer_gets_a_verdict_in_words_to_act_on,
        test_a_vector_is_told_its_features_reprojection_style_and_metadata,
        test_a_raster_is_told_its_size_and_the_conversion_on_arrival,
        test_a_success_says_where_the_layer_is_and_what_changed,
        test_a_stored_but_undisplayable_file_is_a_warning_not_a_success,
        test_a_failure_speaks_in_the_refusers_words_and_a_cancellation_is_quiet,
        test_the_item_link_is_the_web_applications_item_page,
        test_sizes_read_at_a_glance,
    ]
    for test in tests:
        test()
        print("ok  {}".format(test.__name__))
    print("{} checks passed".format(len(tests)))


if __name__ == "__main__":
    main()
