#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove what a raster publish decides, before it costs anyone an upload.

    python scripts/smoke_raster.py

Runs on a plain interpreter — no QGIS, no network. QGIS' classes are stubbed:
what is under test is not GDAL, it is the four judgements this plugin makes
before a single byte moves.

* a layer already stored as a GeoTIFF is **sent as it is** — re-encoding a
  GeoTIFF to obtain a GeoTIFF costs minutes and can only lose information;
* the file it sends is the user's own, so nothing may be marked for deletion;
* a `.vrt`, a subdataset or a path that no longer exists is **not** a file to
  send, however much it looks like one;
* a WMS layer is a connection, not a dataset, and is refused with a sentence
  that says so instead of an empty upload.

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


def _install_qgis_stubs():
    """Stand in for the QGIS classes the modules import at load time.

    None of them is exercised here: every scenario below stops before a writer
    is ever constructed. The stubs exist so the decision code can be imported.
    """
    core = types.ModuleType("qgis.core")
    for name in (
        "QgsCoordinateTransformContext",
        "QgsFeature",
        "QgsFields",
        "QgsField",
        "QgsRasterLayer",
        "QgsRasterPipe",
        "QgsVectorLayer",
        "QgsWkbTypes",
    ):
        setattr(core, name, type(name, (object,), {}))

    class QgsVectorFileWriter(object):
        NoError = 0

    class QgsRasterFileWriter(object):
        NoError = 0

        def __init__(self, path):
            self.path = path

    core.QgsVectorFileWriter = QgsVectorFileWriter
    core.QgsRasterFileWriter = QgsRasterFileWriter

    qtcore = types.ModuleType("qgis.PyQt.QtCore")
    qtcore.QVariant = type("QVariant", (object,), {"Map": 8, "List": 9, "StringList": 11, "Hash": 28})

    qgis = types.ModuleType("qgis")
    pyqt = types.ModuleType("qgis.PyQt")
    qgis.core = core
    qgis.PyQt = pyqt
    pyqt.QtCore = qtcore

    sys.modules.update(
        {"qgis": qgis, "qgis.core": core, "qgis.PyQt": pyqt, "qgis.PyQt.QtCore": qtcore}
    )


_install_qgis_stubs()

from align_connector.publish import raster  # noqa: E402
from align_connector.publish.exporter import ExportFailed  # noqa: E402


class FakeProvider(object):
    def __init__(self, name="gdal", x=100, y=100):
        self._name = name
        self._x = x
        self._y = y

    def name(self):
        return self._name

    def xSize(self):  # noqa: N802 — the QGIS spelling
        return self._x

    def ySize(self):  # noqa: N802
        return self._y


class FakeLayer(object):
    def __init__(self, source, provider=None, name="Elevation", valid=True):
        self._source = source
        self._provider = provider if provider is not None else FakeProvider()
        self._name = name
        self._valid = valid

    def source(self):
        return self._source

    def dataProvider(self):  # noqa: N802
        return self._provider

    def name(self):
        return self._name

    def isValid(self):  # noqa: N802
        return self._valid


FAILURES = []


def check(label, condition):
    print("  {}   {}".format("ok " if condition else "FAIL", label))
    if not condition:
        FAILURES.append(label)


def main():
    workdir = tempfile.mkdtemp(prefix="align-smoke-raster-")
    tif = os.path.join(workdir, "dem.tif")
    with open(tif, "wb") as handle:
        handle.write(b"II*\0not really a tiff, and it does not need to be")
    vrt = os.path.join(workdir, "mosaic.vrt")
    with open(vrt, "w") as handle:
        handle.write("<VRTDataset/>")

    print("a layer already stored as a GeoTIFF")
    result = raster.export_raster(FakeLayer(tif))
    check("is sent as it is", result.path == tif)
    check("keeps the raster content type", result.content_type == "image/tiff")
    check("is never marked for deletion — it is the user's file", result.workdir is None)
    check("and says so", result.reused_source is True)
    check("under a filename ALIGN accepts", result.filename == "Elevation.tif")

    print("open options after the path")
    check("are stripped", raster.source_file_of(FakeLayer(tif + "|band=1")) == tif)

    print("what only looks like a file to send")
    check("a .vrt points elsewhere — refused", raster.source_file_of(FakeLayer(vrt)) is None)
    check(
        "a subdataset is not a path",
        raster.source_file_of(FakeLayer('NETCDF:"{}":elevation'.format(tif))) is None,
    )
    check(
        "a path that no longer exists",
        raster.source_file_of(FakeLayer(os.path.join(workdir, "gone.tif"))) is None,
    )

    print("a remote service")
    try:
        raster.export_raster(FakeLayer("url=https://example.org/wms", FakeProvider(name="wms")))
        check("is refused", False)
    except ExportFailed as exc:
        check("is refused", True)
        check("and the reason names what it is", "connection" in str(exc).lower())
        check("and says where it belongs", "web application" in str(exc))

    print("a raster with no pixels and no file")
    try:
        raster.export_raster(FakeLayer("memory-thing", FakeProvider(x=0, y=0)))
        check("is refused before any writer runs", False)
    except ExportFailed as exc:
        check("is refused before any writer runs", "no pixels" in str(exc))

    print()
    if FAILURES:
        print("{} check(s) failed".format(len(FAILURES)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
