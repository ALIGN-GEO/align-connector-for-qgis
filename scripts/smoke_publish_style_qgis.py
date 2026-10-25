#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove, inside QGIS, what a publish reads from real QGIS objects (PH-37, PH-38).

    python scripts/smoke_publish_style_qgis.py     (with QGIS' Python)

The stand-ins of ``smoke_symbology.py`` and ``smoke_raster.py`` answer by
method name; they cannot own a C++ object, nor carry a coordinate system the
way a raster provider does. Two defects hid there (recette of
ALIGN-GEO/align-connector-for-qgis#1):

* an SVG marker's colour read from a symbol layer whose cloned symbol was
  already freed came back black: a red marker must arrive red, alone or in
  categories, and a pattern must get its colour written;
* a JPEG and its world file, with the system set on the layer in QGIS (the
  files carry none), must be written as a GeoTIFF in that system, not without
  one (ALIGN then placed it in the Netherlands).

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import gc
import os
import pathlib
import shutil
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from osgeo import gdal, osr  # noqa: E402
from qgis.core import (  # noqa: E402
    QgsApplication,
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsFillSymbol,
    QgsMarkerSymbol,
    QgsRasterLayer,
    QgsRendererCategory,
    QgsSingleSymbolRenderer,
    QgsSVGFillSymbolLayer,
    QgsSvgMarkerSymbolLayer,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QColor  # noqa: E402

from align_connector.publish import raster, symbology  # noqa: E402

SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<circle cx="12" cy="12" r="9" fill="param(fill) #000000" stroke="param(outline) #232323" '
    'stroke-width="param(outline-width) 1"/></svg>'
)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def marker(path, color):
    layer = QgsSvgMarkerSymbolLayer(path, 6.0)
    layer.setFillColor(QColor(color))
    layer.setStrokeColor(QColor("#111111"))
    symbol = QgsMarkerSymbol()
    symbol.changeSymbolLayer(0, layer)
    return symbol


def test_a_red_svg_marker_arrives_red(workdir):
    path = os.path.join(workdir, "pin.svg")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(SVG)

    points = QgsVectorLayer("Point?crs=EPSG:4326&field=zone:string", "points", "memory")
    points.setRenderer(QgsSingleSymbolRenderer(marker(path, "#ff0000")))
    gc.collect()
    style = symbology.convert(points).style
    check(style["images"]["markerColor"] == "#ff0000", ("single symbol", style["images"].get("markerColor")))
    check(style["paint"]["circle-color"] == "#ff0000", style["paint"])

    categories = [
        QgsRendererCategory("A", marker(path, "#ff0000"), "A"),
        QgsRendererCategory("B", marker(path, "#00aa00"), "B"),
    ]
    points.setRenderer(QgsCategorizedSymbolRenderer("zone", categories))
    gc.collect()
    style = symbology.convert(points).style
    check(style["images"]["markerColor"] == "#ff0000", ("categorized", style["images"].get("markerColor")))
    check('stroke="#111111"' in style["images"]["markerSvg"], "the outline is written from the live layer")

    fill = QgsSVGFillSymbolLayer(path, 5.0, 0.0)
    fill.setSvgFillColor(QColor("#1d4ed8"))
    symbol = QgsFillSymbol()
    symbol.changeSymbolLayer(0, fill)
    polygons = QgsVectorLayer("Polygon?crs=EPSG:4326", "polygons", "memory")
    polygons.setRenderer(QgsSingleSymbolRenderer(symbol))
    gc.collect()
    pattern = symbology.convert(polygons).style["images"]["fillPatternSvg"]
    check('fill="#1d4ed8"' in pattern, pattern[:200])


def test_a_jpeg_and_its_world_file_keep_the_system_set_in_qgis(workdir):
    source = gdal.GetDriverByName("MEM").Create("", 40, 30, 3, gdal.GDT_Byte)
    for band in range(1, 4):
        source.GetRasterBand(band).Fill(120)
    source.SetGeoTransform((651000.0, 2.5, 0.0, 6863000.0, 0.0, -2.5))
    jpeg = os.path.join(workdir, "ortho.jpg")
    gdal.GetDriverByName("JPEG").CreateCopy(jpeg, source, options=["WORLDFILE=YES"])
    source = None
    for leftover in ("ortho.jpg.aux.xml",):
        with_path = os.path.join(workdir, leftover)
        if os.path.exists(with_path):
            os.remove(with_path)
    os.replace(os.path.join(workdir, "ortho.wld"), os.path.join(workdir, "ortho.jgw"))

    layer = QgsRasterLayer(jpeg, "Ortho", "gdal")
    check(layer.isValid(), "the JPEG opens in QGIS")
    layer.setCrs(QgsCoordinateReferenceSystem("EPSG:2154"))
    result = raster.export_raster(layer, target_dir=workdir)
    written = gdal.Open(result.path)
    srs = osr.SpatialReference(wkt=written.GetProjection())
    srs.AutoIdentifyEPSG()
    check(srs.GetAuthorityCode(None) == "2154", ("the GeoTIFF is in Lambert-93", written.GetProjection()[:120]))
    origin = written.GetGeoTransform()
    check(abs(origin[0] - 651000.0) < 0.01 and abs(origin[3] - 6863000.0) < 0.01, origin)
    written = None
    del layer


def main():
    app = QgsApplication([], False)
    app.initQgis()
    workdir = tempfile.mkdtemp(prefix="align-smoke-style-")
    try:
        for test in (test_a_red_svg_marker_arrives_red, test_a_jpeg_and_its_world_file_keep_the_system_set_in_qgis):
            test(workdir)
            print("ok  {}".format(test.__name__))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        gc.collect()
        app.exitQgis()
    print("2 checks passed")


if __name__ == "__main__":
    main()
