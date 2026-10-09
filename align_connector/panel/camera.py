# -*- coding: utf-8 -*-
"""The camera a web map opens on: what the QGIS canvas shows, in WGS 84.

A web map of references carries no coordinates of its own, so without a
camera the Map builder would open on the world. The canvas extent is what
the publisher was looking at: that is the view to keep.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from qgis.core import Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsMessageLog, QgsProject

from .webmap import map_view_of
from ..i18n import tr
from ..qtcompat import qt_enum


def canvas_map_view(canvas):
    """Centre and zoom of the canvas extent, or None when it cannot be read.

    A camera is a convenience, never a condition: a failure is logged and
    the web map opens on the world.
    """
    try:
        extent = canvas.extent()
        source = canvas.mapSettings().destinationCrs()
        transform = QgsCoordinateTransform(
            source, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance().transformContext()
        )
        rect = transform.transformBoundingBox(extent)
        return map_view_of(
            (rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum()), canvas.width(), canvas.height()
        )
    except Exception as exc:  # noqa: BLE001 — any failure here is a lost convenience, said in the log
        QgsMessageLog.logMessage(
            tr("Could not read the canvas extent for the web map camera: {}").format(exc), "ALIGN Connector", qt_enum(Qgis, "MessageLevel", "Warning")
        )
        return None
