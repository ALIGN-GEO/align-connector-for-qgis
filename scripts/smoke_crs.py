#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove what the plugin decides about a layer's coordinates before writing it.

    python scripts/smoke_crs.py

Runs on a plain interpreter — no QGIS, no network. The transformation itself
belongs to QGIS and can only be proven there; the decision that drives it is
pure, and it is what this script holds (TCK-2199):

* a layer already in WGS 84 (EPSG:4326, or OGC:CRS84) leaves untouched;
* any other system — Lambert 72, RD New, Web Mercator, a custom one — is
  converted to WGS 84, and the sentence shown before the send says so;
* a layer with no usable system is refused, with the sentence that says what
  to fix, before any byte moves.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import pathlib
import sys
import types

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _install_qgis_stubs():
    """Stand in for the QGIS classes the exporter imports at load time."""
    core = types.ModuleType("qgis.core")
    for name in (
        "QgsCoordinateReferenceSystem",
        "QgsCoordinateTransform",
        "QgsFeature",
        "QgsFields",
        "QgsField",
        "QgsProject",
        "QgsRasterLayer",
        "QgsVectorLayer",
        "QgsWkbTypes",
    ):
        setattr(core, name, type(name, (object,), {}))

    class QgsVectorFileWriter(object):
        NoError = 0

    core.QgsVectorFileWriter = QgsVectorFileWriter

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

from align_connector.publish import exporter  # noqa: E402


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def test_wgs84_is_kept_as_it_is():
    for authid in ("EPSG:4326", "OGC:CRS84", "epsg:4326", " EPSG:4326 "):
        check(exporter.crs_plan(authid) == exporter.KEEP, authid)
        check(exporter.reprojection_notice(authid) is None, "nothing to announce for {}".format(authid))


def test_every_other_system_is_transformed():
    for authid in ("EPSG:31370", "EPSG:28992", "EPSG:3857", "EPSG:2154", "USER:100001"):
        check(exporter.crs_plan(authid) == exporter.TRANSFORM, authid)
        notice = exporter.reprojection_notice(authid)
        check(notice.startswith("Coordinates will be converted from {} to WGS 84 (EPSG:4326)".format(authid)), notice)
        check("not changed" in notice, "the user must know the layer itself is untouched")


def test_a_custom_system_without_an_id_still_transforms():
    check(exporter.crs_plan("", valid=True) == exporter.TRANSFORM, "valid but unregistered")
    notice = exporter.reprojection_notice("", valid=True)
    check("its custom coordinate system" in notice, notice)


def test_no_system_is_refused_before_anything_moves():
    for authid in ("", "EPSG:4326", "EPSG:31370"):
        check(exporter.crs_plan(authid, valid=False) == exporter.REFUSE, "invalid means refuse, whatever the id says")
    notice = exporter.reprojection_notice("", valid=False)
    check("no coordinate reference system" in notice and "before publishing" in notice, notice)


def test_the_target_is_not_a_choice():
    check(exporter.TARGET_AUTHID == "EPSG:4326", "the viewer reads longitude/latitude and nothing else")


def main():
    tests = [
        test_wgs84_is_kept_as_it_is,
        test_every_other_system_is_transformed,
        test_a_custom_system_without_an_id_still_transforms,
        test_no_system_is_refused_before_anything_moves,
        test_the_target_is_not_a_choice,
    ]
    for test in tests:
        test()
        print("ok  {}".format(test.__name__))
    print("{} checks passed".format(len(tests)))


if __name__ == "__main__":
    main()
