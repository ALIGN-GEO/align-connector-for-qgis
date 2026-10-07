# -*- coding: utf-8 -*-
"""What a publish will do with one layer, said before it starts.

A briefing is the whole "analyze" step of the panel, in one object: what
will travel (features or a file, and how heavy), what changes on the way
(reprojection, conversion on arrival), what the style and the metadata
become, and every simplification a publisher should know first. It reads
the layer on the interface thread (QGIS layers are not for other threads)
and hands the task what it needs to send: the converted style, the metadata.

Pure given a layer that answers the QGIS method names; proven by
``scripts/smoke_panel.py``.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import os

from ..i18n import tr
from . import isometa
from . import raster
from . import symbology
from .exporter import publishable_kind, reprojection_notice
from .wording import describe_style, readable_size


class Briefing(object):
    """One layer, explained.

    :param kind: ``'vector'``, ``'raster'`` or ``None`` (cannot be published)
    :param text: the sentences a publisher reads
    :param style: the converted style to send, or None
    :param iso: the metadata to send, or None
    :param title: the title to propose (metadata title, else layer name)
    """

    def __init__(self, kind, text, style=None, iso=None, title=None):
        self.kind = kind
        self.text = text
        self.style = style
        self.iso = iso
        self.title = title


def brief(layer, selected_only=False):
    """Explain what publishing this layer will do."""
    kind = publishable_kind(layer)
    reading = isometa.read(layer)
    name = _call(layer, "name", "") or tr("Layer")
    title = reading.title or name

    if kind == "raster":
        text = _raster_text(layer) + " " + isometa.describe_metadata(reading)
        return Briefing(kind, text, None, reading.iso, title)

    if kind == "vector":
        text = _vector_text(layer, selected_only)
        crs = _call(layer, "crs", None)
        notice = reprojection_notice(_call(crs, "authid", "") or "", _call(crs, "isValid", True))
        if notice:
            text += " " + notice
        conversion = symbology.convert(layer)
        text += " " + describe_style(conversion)
        text += " " + isometa.describe_metadata(reading)
        return Briefing(kind, text, conversion.style, reading.iso, title)

    return Briefing(None, tr("ALIGN publishes vector and raster layers. This layer is neither."), None, None, title)


def _vector_text(layer, selected_only):
    selected = _call(layer, "selectedFeatureCount", 0) or 0
    total = _call(layer, "featureCount", 0) or 0
    count = selected if selected_only and selected > 0 else total
    return tr(
        "{} features will be written and uploaded. Large layers keep uploading in the "
        "background: you can carry on working, and an interrupted transfer resumes."
    ).format(count)


def _raster_text(layer):
    """How much will travel, and that the file is normalised on arrival: both
    said now, not discovered in the Datastore afterwards."""
    source = raster.source_file_of(layer)
    if source:
        size = readable_size(_size_of(source))
        return tr(
            "This layer's GeoTIFF will be sent as it is{}. ALIGN converts it to a "
            "Cloud-Optimized GeoTIFF on arrival; if that conversion has to reproject the "
            "data, your original is kept alongside it and both count against your storage."
        ).format(" ({})".format(size) if size else "")
    return tr(
        "A compressed GeoTIFF will be written from this layer and sent. ALIGN converts it "
        "to a Cloud-Optimized GeoTIFF on arrival; if that conversion has to reproject the "
        "data, the file you sent is kept alongside it and both count against your storage."
    )


def _size_of(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def _call(target, name, default=None):
    if target is None:
        return default
    method = getattr(target, name, None)
    if not callable(method):
        return default
    try:
        return method()
    except Exception:  # noqa: BLE001 — a binding gap reads as "unknown", never a crash in a briefing
        return default
