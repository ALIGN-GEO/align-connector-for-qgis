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

**Complex fields are flattened to JSON text before writing.** Open-data layers
routinely carry map or list attributes — an OpenDataSoft export gives every
feature a `geo_point_2d` map — and no GIS file format accepts them. Writing
would fail per feature with "invalid type for field", which reads to the user
as "this plugin cannot publish my layer". Serialising to text keeps the value
and lets the publish through; dropping the field would be quieter and worse.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import os
import re
import tempfile

from qgis.core import (
    QgsCoordinateTransformContext,
    QgsFeature,
    QgsFields,
    QgsField,
    QgsVectorFileWriter,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant

#: Formats we can write, best first. `(driver, extension, content type)`.
_FORMATS = (
    ("FlatGeobuf", "fgb", "application/x-flatgeobuf"),
    ("GeoJSON", "geojson", "application/geo+json"),
)


class ExportFailed(Exception):
    """The layer could not be written, with a reason fit to show a human."""


class ExportResult(object):
    """What the publish will send — and what may be deleted afterwards.

    ``workdir`` is the directory *this module created*, or ``None`` when the
    file belongs to the user. That distinction is the whole point of the class:
    a raster already stored as a GeoTIFF is sent untouched, and the cleanup step
    must never be able to reach into the user's own folder.
    """

    def __init__(
        self, path, filename, content_type, workdir=None, flattened=(), reused_source=False,
        already_servable=False,
    ):
        self.path = path
        self.filename = filename
        self.content_type = content_type
        self.workdir = workdir
        #: Attributes serialised to JSON text to make the layer writable.
        self.flattened = list(flattened)
        #: True when ``path`` is the user's own file, not a copy we produced.
        self.reused_source = reused_source
        #: True when this file is VERIFIED to need no server-side conversion.
        #: Declared to ALIGN so the conversion ceiling does not apply to it.
        self.already_servable = already_servable


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



#: Attribute types no vector format can store. Serialised to JSON text instead.
_COMPLEX_TYPES = (QVariant.Map, QVariant.List, QVariant.StringList, QVariant.Hash)


def _is_complex(field):
    return field.type() in _COMPLEX_TYPES


def _as_text(value):
    """A JSON rendering of a complex value, stable enough to be read back."""
    import json

    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def flatten_complex_fields(layer, selected_only=False):
    """Return a layer whose complex attributes are JSON text.

    Returns the original layer untouched when there is nothing to flatten —
    the common case, and the one that must stay free.

    :returns: ``(layer_to_write, flattened_field_names)``
    """
    complex_names = [f.name() for f in layer.fields() if _is_complex(f)]
    if not complex_names:
        return layer, []

    fields = QgsFields()
    for field in layer.fields():
        if _is_complex(field):
            fields.append(QgsField(field.name(), QVariant.String))
        else:
            fields.append(field)

    uri = "{}?crs={}".format(
        QgsWkbTypes.displayString(layer.wkbType()),
        layer.crs().authid() or "EPSG:4326",
    )
    flat = QgsVectorLayer(uri, layer.name(), "memory")
    flat.dataProvider().addAttributes(fields.toList())
    flat.updateFields()

    source = layer.getSelectedFeatures() if selected_only else layer.getFeatures()
    written = []
    for feature in source:
        copy = QgsFeature(flat.fields())
        copy.setGeometry(feature.geometry())
        values = []
        for field in layer.fields():
            value = feature[field.name()]
            values.append(_as_text(value) if _is_complex(field) else value)
        copy.setAttributes(values)
        written.append(copy)
    flat.dataProvider().addFeatures(written)
    flat.updateExtents()

    return flat, complex_names

def export_layer(layer, selected_only=False, target_dir=None):
    """Write ``layer`` to a temporary file.

    :param selected_only: write only the selected features
    :returns: an :class:`ExportResult`
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

    # A flattened copy already holds only the requested features, so the
    # writer must not filter a second time.
    source, flattened = flatten_complex_fields(layer, selected_only=selected_only)
    only_selected = bool(selected_only) and source is layer

    for driver, extension, content_type in available_formats():
        filename = safe_basename(layer.name(), extension)
        path = os.path.join(directory, filename)

        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = driver
        options.fileEncoding = "UTF-8"
        options.onlySelectedFeatures = only_selected

        error, message = _write(source, path, options)
        if error == QgsVectorFileWriter.NoError:
            if not os.path.exists(path) or os.path.getsize(path) == 0:
                last_error = "the writer produced an empty file"
                continue
            return ExportResult(
                path=path,
                filename=filename,
                content_type=content_type,
                workdir=directory,
                flattened=flattened,
            )

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


# ---------------------------------------------------------------------------
# The one place that answers "what can this plugin send?"
# ---------------------------------------------------------------------------

def publishable_kind(layer):
    """``'vector'``, ``'raster'``, or ``None`` when this layer cannot be sent.

    The dialog and the export share this function on purpose. Offering a layer
    the export would then refuse — or hiding one it could have handled — is the
    kind of drift two separate lists produce within a release.
    """
    if layer is None:
        return None
    try:
        if not layer.isValid():
            return None
    except AttributeError:
        return None
    if isinstance(layer, QgsVectorLayer):
        return "vector"
    if isinstance(layer, QgsRasterLayer):
        return "raster"
    return None


def export_for_publish(layer, selected_only=False):
    """Write whatever this layer is into a file ALIGN accepts.

    :returns: an :class:`ExportResult`
    :raises ExportFailed: when the layer is of a kind the plugin does not send
    """
    kind = publishable_kind(layer)
    if kind == "vector":
        return export_layer(layer, selected_only=selected_only)
    if kind == "raster":
        # Imported here, not at module load: the raster half imports this
        # module for its result type, so a top-level import would be a cycle.
        from .raster import export_raster

        return export_raster(layer)
    raise ExportFailed(
        "ALIGN can publish vector and raster layers. “{}” is neither.".format(
            layer.name() if layer is not None else "This layer"
        )
    )
