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

**Every vector file leaves in WGS 84 (EPSG:4326).** ALIGN stores a vector as it
arrives and its viewer reads longitude/latitude: a file in metres (Lambert 72,
RD New) would land outside the world, silently. Reprojecting here is lossless
for a vector and uses the project's own transformation context, datum shifts
and grids included; it is announced before the send, and the original system
travels in the provenance (TCK-2199). A raster goes the other way — reprojected
on the server, original kept — because a reprojected raster is not lossless.

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
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFields,
    QgsField,
    QgsProject,
    QgsVectorFileWriter,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsVectorTileLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant

from ..i18n import tr

#: Formats we can write, best first. `(driver, extension, content type)`.
_FORMATS = (
    ("FlatGeobuf", "fgb", "application/x-flatgeobuf"),
    ("GeoJSON", "geojson", "application/geo+json"),
)

#: Cloud-native vector files the viewer reads as they are (TCK-2206, rule 22:
#: an upload keeps its format). Sent untouched when nothing has to change on
#: the way: `extension → (extension sent, content type)`.
_PASSTHROUGH_VECTOR = {
    ".fgb": ("fgb", "application/x-flatgeobuf"),
    ".parquet": ("parquet", "application/vnd.apache.parquet"),
    ".geoparquet": ("parquet", "application/vnd.apache.parquet"),
}


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
        already_servable=False, source_crs=None, reprojected=False,
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
        #: The layer's own coordinate system (``"EPSG:31370"``), recorded in the
        #: provenance so the origin is never lost; ``None`` when unknown.
        self.source_crs = source_crs
        #: True when the file was written in WGS 84 from another system.
        self.reprojected = reprojected


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


# ---------------------------------------------------------------------------
# Coordinates: every vector leaves in WGS 84 (TCK-2199)
# ---------------------------------------------------------------------------

#: The system every vector file is written in. Not a choice offered to the
#: user: the viewer reads nothing else, and the display projection (Web
#: Mercator, or the globe) is the viewer's business, not the file's.
TARGET_AUTHID = "EPSG:4326"

#: Identifiers that already mean longitude/latitude on WGS 84.
_WGS84_AUTHIDS = ("EPSG:4326", "OGC:CRS84")

KEEP = "keep"
TRANSFORM = "transform"
REFUSE = "refuse"


def crs_plan(authid, valid=True):
    """Decide what to do with a layer's coordinates before writing it.

    Pure, so the decision is proven without QGIS. ``valid`` is what
    ``QgsCoordinateReferenceSystem.isValid()`` says; ``authid`` is the authority
    identifier (``"EPSG:31370"``), empty when the system has none.

    :returns: ``KEEP`` (already WGS 84), ``TRANSFORM``, or ``REFUSE`` — a layer
        without a usable system cannot be placed by anyone, and sending it
        would only move the silence to the map.
    """
    if not valid:
        return REFUSE
    normalised = (authid or "").strip().upper()
    if not normalised:
        # A valid custom system without an authority id still transforms:
        # QGIS knows it, even though nobody registered it.
        return TRANSFORM
    return KEEP if normalised in _WGS84_AUTHIDS else TRANSFORM


def reprojection_notice(authid, valid=True):
    """The sentence a publisher reads before sending, or ``None`` when there is nothing to say."""
    plan = crs_plan(authid, valid)
    if plan == REFUSE:
        return tr(
            "This layer has no coordinate reference system. Set one (Layer Properties, "
            "Source) before publishing."
        )
    if plan == TRANSFORM:
        origin = (authid or "").strip() or tr("its custom coordinate system")
        return tr(
            "Coordinates will be converted from {} to WGS 84 (EPSG:4326) on export; "
            "the layer itself is not changed."
        ).format(origin)
    return None


def vector_passthrough(source, provider, authid, valid=True, selected_only=False):
    """The layer's own file, when it can leave as it is. Pure.

    Three conditions, each one a reason to rewrite otherwise: an OGR file in a
    format the viewer reads untouched; already in WGS 84 (the viewer's FGB
    door never reprojects, TCK-2199); the whole layer (a selection is a
    subset, not the file).

    :returns: ``(path, extension, content_type)`` or ``None``
    """
    if selected_only or (provider or "").lower() != "ogr":
        return None
    candidate = (source or "").split("|")[0].strip()
    entry = _PASSTHROUGH_VECTOR.get(os.path.splitext(candidate)[1].lower())
    if entry is None or crs_plan(authid, valid) != KEEP:
        return None
    return candidate, entry[0], entry[1]


def passthrough_source_of(layer, selected_only=False):
    """`vector_passthrough` for a real layer, with the file checked on disk."""
    crs = layer.crs()
    provider = layer.dataProvider()
    ready = vector_passthrough(
        layer.source(), provider.name() if provider is not None else "", crs.authid(), crs.isValid(), selected_only
    )
    if ready is None or not os.path.isfile(ready[0]):
        return None
    return ready


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

    flat = QgsVectorLayer(QgsWkbTypes.displayString(layer.wkbType()), layer.name(), "memory")
    # The copy keeps the layer's own system, whatever it is; the writer is what
    # brings it to WGS 84, once, for the copy and the original alike.
    flat.setCrs(layer.crs())
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
    """Write ``layer`` to a temporary file, in WGS 84.

    :param selected_only: write only the selected features
    :returns: an :class:`ExportResult`
    :raises ExportFailed: when nothing could be written, or when the layer has
        no coordinate reference system
    """
    if layer is None:
        raise ExportFailed("No layer selected.")
    if not layer.isValid():
        raise ExportFailed("That layer is not valid — QGIS cannot read it.")

    crs = layer.crs()
    plan = crs_plan(crs.authid(), crs.isValid())
    if plan == REFUSE:
        raise ExportFailed(reprojection_notice(crs.authid(), crs.isValid()))

    feature_count = layer.selectedFeatureCount() if selected_only else layer.featureCount()
    if feature_count == 0:
        raise ExportFailed(
            tr("There is nothing to publish: the selection is empty.")
            if selected_only
            else tr("That layer has no features.")
        )

    # A file the viewer reads as it is leaves as it is (TCK-2206): nothing is
    # rewritten, nothing is reprojected, nothing is deleted afterwards.
    ready = passthrough_source_of(layer, selected_only)
    if ready is not None:
        path, extension, content_type = ready
        return ExportResult(
            path=path,
            filename=safe_basename(layer.name(), extension),
            content_type=content_type,
            workdir=None,
            reused_source=True,
            source_crs=crs.authid() or None,
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
        if plan == TRANSFORM:
            options.ct = QgsCoordinateTransform(
                crs,
                QgsCoordinateReferenceSystem(TARGET_AUTHID),
                QgsProject.instance().transformContext(),
            )

        error, message = _write(source, path, options)
        if error == QgsVectorFileWriter.NoError:
            if not os.path.exists(path) or os.path.getsize(path) == 0:
                last_error = tr("the writer produced an empty file")
                continue
            return ExportResult(
                path=path,
                filename=filename,
                content_type=content_type,
                workdir=directory,
                flattened=flattened,
                source_crs=crs.authid() or None,
                reprojected=plan == TRANSFORM,
            )

        last_error = message or tr("the writer refused the layer")

    raise ExportFailed(tr("Could not write this layer to a publishable file ({}).").format(last_error))


def _write(layer, path, options):
    """Call whichever writer this QGIS build exposes.

    ``writeAsVectorFormatV3`` is the current API; older builds in our supported
    range only have V2. The difference is the shape of the return value, not
    the behaviour — so it is absorbed here rather than in the caller.

    The transformation context is the project's: it carries the datum shifts
    and grids the user chose, which a bare context would silently drop.
    """
    context = QgsProject.instance().transformContext()

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
    """``'vector'``, ``'raster'``, ``'archive'`` (a vector tile archive on
    disk, TCK-2206), or ``None`` when this layer cannot be sent.

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
    if isinstance(layer, QgsVectorTileLayer):
        return "archive"
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
    if kind == "archive":
        from .tiles import export_archive

        return export_archive(layer)
    raise ExportFailed(
        tr("ALIGN can publish vector and raster layers. “{}” is neither.").format(
            layer.name() if layer is not None else tr("This layer")
        )
    )
