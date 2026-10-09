# -*- coding: utf-8 -*-
"""Turning a QGIS raster layer into a file ALIGN can serve.

The short version: **send a GeoTIFF and let ALIGN do the rest.** The platform
derives the Cloud-Optimized GeoTIFF itself, server-side, at the moment it
catalogues the upload — so there is no COG to build here, and building one
would only mean two conversions instead of one.

Two paths, and the first one matters more than it looks:

* the layer is **already backed by a local ``.tif``** — send that file, byte for
  byte. Re-encoding a GeoTIFF to produce a GeoTIFF costs minutes, costs disk,
  and can only lose information;
* anything else GDAL can read — write one, compressed, and send that.

**A file that is already the serving target is declared as such.** ALIGN
converts a GeoTIFF to a Cloud-Optimized GeoTIFF in EPSG:3857 on arrival, and
that conversion has a size ceiling. A file that already IS that has nothing to
convert, so the ceiling does not apply to it — but only if someone checks the
header and says so. QGIS ships GDAL; it is better placed than the server to
check, since the file is right here. The claim is made only after verifying it:
the server re-verifies anyway, and a wrong claim would simply route the file
through the conversion it said it did not need.

What is deliberately refused: layers that are not datasets. A WMS or WCS layer
is a *connection*, not a file; there is nothing to upload, and pretending
otherwise would produce an empty asset. ALIGN registers remote services from
the web app, which is where that belongs.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import os
import tempfile

from qgis.core import (
    QgsCoordinateTransformContext,
    QgsRasterFileWriter,
    QgsRasterPipe,
)

from .exporter import ExportFailed, ExportResult, safe_basename
from ..i18n import tr
from ..qtcompat import qt_enum

#: The one content type that makes ALIGN recognise a raster upload. Sending
#: `application/octet-stream` would upload fine and never derive anything: the
#: platform infers the GIS format from the filename and this header, and an
#: undetermined format simply skips the COG step.
RASTER_CONTENT_TYPE = "image/tiff"

#: Extensions we can forward untouched. A `.vrt` is deliberately absent: it is
#: a few lines of XML pointing at files that would stay on this machine. An
#: MBTiles raster leaves as it is too (TCK-2206): ALIGN derives a PMTiles from
#: it, where a GeoTIFF written from it would flatten a tile pyramid.
_PASSTHROUGH_EXTENSIONS = (".tif", ".tiff", ".mbtiles")


def is_archive(path):
    return (path or "").lower().endswith(".mbtiles")


def content_type_of(path):
    """What the upload door must be told: a GeoTIFF by its own type, an
    archive as bytes (ALIGN reads the GIS format from the extension)."""
    return "application/octet-stream" if is_archive(path) else RASTER_CONTENT_TYPE

#: GDAL creation options for the written GeoTIFF. DEFLATE is lossless and valid
#: for every band type — the layout is ALIGN's business, the size is ours: this
#: is what travels over the wire.
_CREATE_OPTIONS = ["COMPRESS=DEFLATE", "TILED=YES"]


def source_file_of(layer):
    """The layer's own file, when it has one that can be sent as it is.

    GDAL sources carry more than paths — a subdataset (``NETCDF:"f.nc":var``),
    open options after a pipe. Only a plain, existing GeoTIFF qualifies.

    :returns: the path, or ``None`` when there is nothing reusable.
    """
    try:
        source = layer.source() or ""
    except AttributeError:  # pragma: no cover — not a layer we can publish
        return None
    candidate = source.split("|")[0].strip()
    if not candidate.lower().endswith(_PASSTHROUGH_EXTENSIONS):
        return None
    return candidate if os.path.isfile(candidate) else None


def is_serving_ready(path):
    """True when this file is already what ALIGN serves: a 3857 COG.

    Mirrors the server's own verdict (`decideTransform`): the CRS must be
    EPSG:3857 and the layout must be COG. Anything else — including a perfectly
    good COG in another projection — needs work, and must not be declared as
    needing none.

    Returns False on any doubt. A false negative costs a size check; a false
    positive would send a file into a ceiling that was skipped for it.
    """
    try:
        from osgeo import gdal, osr  # noqa: F401 — QGIS ships the GDAL bindings
    except ImportError:  # pragma: no cover — a QGIS build without the bindings
        return False

    dataset = None
    try:
        gdal.UseExceptions()
        dataset = gdal.Open(path)
        if dataset is None:
            return False
        if (dataset.GetMetadata("IMAGE_STRUCTURE") or {}).get("LAYOUT") != "COG":
            return False
        reference = dataset.GetSpatialRef()
        if reference is None:
            return False
        return reference.GetAuthorityCode(None) == "3857"
    except Exception:  # noqa: BLE001 — a probe that fails proves nothing
        return False
    finally:
        dataset = None


def export_raster(layer, target_dir=None):
    """Produce the GeoTIFF this publish will send.

    :returns: an :class:`~.exporter.ExportResult`
    :raises ExportFailed: when the layer is not a dataset, or cannot be written
    """
    if layer is None or not layer.isValid():
        raise ExportFailed(tr("That layer is not valid: QGIS cannot read it."))

    provider = layer.dataProvider()
    if provider is None:
        raise ExportFailed(tr("QGIS cannot read this raster's data."))

    # A remote service has no bytes to publish. Say so plainly rather than
    # uploading an empty file and letting the Datastore explain it later.
    name = provider.name()
    if name != "gdal":
        raise ExportFailed(
            tr(
                "“{}” is a {} connection, not a dataset: there is no file to publish. "
                "Remote services are added to ALIGN from the web application."
            ).format(layer.name(), name.upper())
        )

    existing = source_file_of(layer)
    if existing:
        archive = is_archive(existing)
        return ExportResult(
            path=existing,
            filename=safe_basename(layer.name(), "mbtiles" if archive else "tif"),
            content_type=content_type_of(existing),
            workdir=None,
            reused_source=True,
            already_servable=False if archive else is_serving_ready(existing),
        )

    if provider.xSize() <= 0 or provider.ySize() <= 0:
        raise ExportFailed(tr("This raster has no pixels to write."))

    directory = target_dir or tempfile.mkdtemp(prefix="align-publish-")
    filename = safe_basename(layer.name(), "tif")
    path = os.path.join(directory, filename)

    pipe = QgsRasterPipe()
    if not pipe.set(provider.clone()):
        raise ExportFailed(tr("QGIS could not open this raster's data to write it out."))

    writer = QgsRasterFileWriter(path)
    writer.setOutputFormat("GTiff")
    writer.setCreateOptions(_CREATE_OPTIONS)

    error = _write(writer, pipe, provider)
    if error != qt_enum(QgsRasterFileWriter, "WriterError", "NoError"):
        raise ExportFailed(tr("Could not write this raster to a publishable file (GDAL error {}).").format(error))
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        raise ExportFailed(tr("Could not write this raster to a publishable file (the writer produced nothing)."))

    return ExportResult(
        path=path,
        filename=filename,
        content_type=RASTER_CONTENT_TYPE,
        workdir=directory,
        reused_source=False,
    )


def _write(writer, pipe, provider):
    """Call whichever ``writeRaster`` this QGIS build exposes.

    Recent builds take a coordinate-transform context; older ones in our
    supported range do not. Same behaviour, one argument apart — absorbed here
    rather than at the call site.
    """
    args = (pipe, provider.xSize(), provider.ySize(), provider.extent(), provider.crs())
    try:
        return writer.writeRaster(*args, QgsCoordinateTransformContext())
    except TypeError:  # pragma: no cover — only reachable on older QGIS builds
        return writer.writeRaster(*args)
