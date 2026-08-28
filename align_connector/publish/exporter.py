# -*- coding: utf-8 -*-
"""Turning a QGIS layer into a file ALIGN accepts.

A QGIS layer is not a file: it may be a database table, a WFS response, a
selection, or a file in a format ALIGN does not take. Publishing therefore
starts by writing one — and *which* format is written matters more than it
looks:

* **FlatGeobuf** is the default. It is binary, compact, keeps field types, and
  streams — all of which count when the result is about to be pushed over a
  slow uplink for an hour.
* **GeoJSON** is the fallback, for the rare build where the FlatGeobuf driver
  is missing. It is universal, but larger and lossier on types.

The layer's own CRS is preserved: ALIGN reprojects server-side, and converting
here would lose precision for no reason.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import os
import re
import tempfile

from qgis.core import QgsCoordinateTransformContext, QgsVectorFileWriter

#: Formats we can write, best first. `(driver, extension, content type)`.
_FORMATS = (
    ("FlatGeobuf", "fgb", "application/x-flatgeobuf"),
    ("GeoJSON", "geojson", "application/geo+json"),
)


class ExportFailed(Exception):
    """The layer could not be written, with a reason fit to show a human."""


def safe_basename(name, extension):
    """A filename ALIGN will accept, derived from the layer's name.

    The server rejects path separators and traversal segments outright, so the
    slug is built from what is left rather than trusting a layer name typed by
    someone else.
    """
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "layer").strip()).strip("-._")
    stem = stem[:80] or "layer"
    return "{}.{}".format(stem, extension)


def available_formats():
    """The formats this QGIS build can actually write, best first."""
    drivers = {driver.driverName for driver in QgsVectorFileWriter.ovrDriverList()} if hasattr(
        QgsVectorFileWriter, "ovrDriverList"
    ) else None
    if drivers is None:
        return _FORMATS
    return tuple(fmt for fmt in _FORMATS if fmt[0] in drivers) or _FORMATS


def export_layer(layer, selected_only=False, target_dir=None):
    """Write ``layer`` to a temporary file.

    :param selected_only: write only the selected features
    :returns: ``(path, filename, content_type)``
    :raises ExportFailed: when nothing could be written
    """
    if layer is None:
        raise ExportFailed("No layer selected.")
    if not layer.isValid():
        raise ExportFailed("That layer is not valid — QGIS cannot read it.")

    feature_count = layer.selectedFeatureCount() if selected_only else layer.featureCount()
    if feature_count == 0:
        raise ExportFailed(
            "There is nothing to publish: the selection is empty."
            if selected_only
            else "That layer has no features."
        )

    directory = target_dir or tempfile.mkdtemp(prefix="align-publish-")
    last_error = None

    for driver, extension, content_type in available_formats():
        filename = safe_basename(layer.name(), extension)
        path = os.path.join(directory, filename)

        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = driver
        options.fileEncoding = "UTF-8"
        options.onlySelectedFeatures = bool(selected_only)

        error, message = _write(layer, path, options)
        if error == QgsVectorFileWriter.NoError:
            if not os.path.exists(path) or os.path.getsize(path) == 0:
                last_error = "the writer produced an empty file"
                continue
            return path, filename, content_type

        last_error = message or "the writer refused the layer"

    raise ExportFailed("Could not write this layer to a publishable file ({}).".format(last_error))


def _write(layer, path, options):
    """Call whichever writer this QGIS build exposes.

    ``writeAsVectorFormatV3`` is the current API; older builds in our supported
    range only have V2. The difference is the shape of the return value, not
    the behaviour — so it is absorbed here rather than in the caller.
    """
    context = QgsCoordinateTransformContext()

    if hasattr(QgsVectorFileWriter, "writeAsVectorFormatV3"):
        result = QgsVectorFileWriter.writeAsVectorFormatV3(layer, path, context, options)
    else:  # pragma: no cover — only reachable on older QGIS builds
        result = QgsVectorFileWriter.writeAsVectorFormatV2(layer, path, context, options)

    error = result[0]
    message = result[1] if len(result) > 1 else ""
    return error, message
