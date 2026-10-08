# -*- coding: utf-8 -*-
"""Vector tile archives: a PMTiles or an MBTiles the project reads from disk
leaves as it is (TCK-2206, rule 22).

QGIS shows such an archive as a vector tile layer. ALIGN stores a PMTiles as
it is and derives a PMTiles from an MBTiles on arrival; the Map builder reads
the archive's own layer list when the item is added by hand. A web map
composed from QGIS cannot read the archive, so the names of the tile layers
the QGIS style paints travel with the request.

The URI is read through QGIS's own decoder (`QgsProviderRegistry.decodeUri`,
rule 14), never parsed by hand; the decision on what it says is pure, and
proven by ``scripts/smoke_cloud.py``.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import os
import re

from qgis.core import QgsProviderRegistry

from ..i18n import tr
from .exporter import ExportFailed, ExportResult, safe_basename

#: The content type the upload door accepts for an archive; ALIGN reads the
#: GIS format from the extension.
ARCHIVE_CONTENT_TYPE = "application/octet-stream"

_ARCHIVE_KINDS = ("mbtiles", "pmtiles")


class ArchivePlan(object):
    """What a vector tile layer's source is, and whether it can leave.

    :param kind: ``'mbtiles'``, ``'pmtiles'``, or ``None``
    :param path: the local file, when there is one
    :param reason: why it cannot be published, or ``None``
    """

    def __init__(self, kind, path=None, reason=None):
        self.kind = kind
        self.path = path
        self.reason = reason

    @property
    def publishable(self):
        return self.reason is None


def archive_plan(decoded):
    """Decide from QGIS's decoded vector-tile URI. Pure."""
    kind = (decoded.get("type") or "").lower()
    location = _local_path(decoded.get("path") or decoded.get("url") or "")
    if kind in _ARCHIVE_KINDS:
        if location.lower().startswith(("http://", "https://")):
            return ArchivePlan(
                kind,
                reason=tr("A remote {} archive: ALIGN takes it as a reference, which a later version of the plugin will publish.").format(kind.upper()),
            )
        if not location:
            return ArchivePlan(kind, reason=tr("The archive has no file path QGIS can give."))
        return ArchivePlan(kind, path=location)
    if kind == "xyz":
        return ArchivePlan(None, reason=tr("A vector tile service: ALIGN takes it as a reference, which a later version of the plugin will publish."))
    if kind == "vtpk":
        return ArchivePlan(None, reason=tr("An ArcGIS vector tile package is not a format the Map builder reads; export it to MBTiles first."))
    return ArchivePlan(None, reason=tr("A vector tile source ALIGN does not take from QGIS."))


def decoded_source(layer):
    """QGIS's own reading of the layer's URI, or an empty dict."""
    try:
        return QgsProviderRegistry.instance().decodeUri("vectortile", layer.source()) or {}
    except Exception:  # noqa: BLE001 — a binding gap reads as "nothing decoded", the verdict says so
        return {}


def archive_of(layer):
    """The plan for a real layer: decoded, and the file checked on disk."""
    plan = archive_plan(decoded_source(layer))
    if plan.path and not os.path.isfile(plan.path):
        return ArchivePlan(plan.kind, reason=tr("The archive file is missing on disk: {}").format(plan.path))
    return plan


def source_layers_of(layer):
    """The names of the tile layers QGIS paints, in style order, without
    repeats: what a web map needs to place the archive."""
    names = []

    def add(name):
        if name and name not in names:
            names.append(name)

    for name in _call(layer, "sourceLayers", None) or []:
        add(_text(name))
    for holder in (_call(layer, "renderer", None), _call(layer, "labeling", None)):
        for style in _call(holder, "styles", None) or []:
            add(_text(_call(style, "layerName", "")))
    return names


def export_archive(layer):
    """The archive itself, untouched: nothing is written, nothing is deleted after."""
    plan = archive_of(layer)
    if not plan.publishable:
        raise ExportFailed(plan.reason)
    return ExportResult(
        path=plan.path,
        filename=safe_basename(layer.name(), plan.kind),
        content_type=ARCHIVE_CONTENT_TYPE,
        workdir=None,
        reused_source=True,
    )


def _local_path(location):
    """``file:///C:/x.mbtiles`` and ``file:///home/x.mbtiles`` read as paths."""
    if location.lower().startswith("file://"):
        location = location[7:]
        if re.match(r"^/[A-Za-z]:", location):
            location = location[1:]
    return location


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _call(target, name, default=None):
    if target is None:
        return default
    method = getattr(target, name, None)
    if not callable(method):
        return default
    try:
        return method()
    except Exception:  # noqa: BLE001 — a binding gap reads as unknown, never a crash in a plan
        return default
