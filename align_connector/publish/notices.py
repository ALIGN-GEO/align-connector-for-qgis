# -*- coding: utf-8 -*-
"""What ALIGN says about a publication, in the language of QGIS (TCK-2348, CA-12).

ALIGN sends each notice as a code, its parameters, and an English message.
The codes this plugin knows are said in the user's language; a code it does
not know keeps ALIGN's message, so a newer server never leaves the user
without the sentence. The codes mirror ALIGN's
``feature-publication-notices.golden.json``.

Parameters are names from the user's own file: they are escaped, because the
message bar shows rich text.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import html

from ..i18n import tr


def notice_text(notice):
    """One notice as a sentence. ``notice`` is ``{code, params, message}``."""
    if not isinstance(notice, dict):
        return html.escape(str(notice), quote=False)
    params = notice.get("params") if isinstance(notice.get("params"), dict) else {}
    value = {key: html.escape(str(item), quote=False) for key, item in params.items()}
    code = notice.get("code")
    if code == "fieldBinaryDropped" and "field" in value:
        return tr("The field “{}” holds binary data and is not published.").format(value["field"])
    if code == "fieldRenamed" and {"from", "to"} <= set(value):
        if value.get("reserved") == "geom":
            return tr("The field “{}” is kept as “{}”: the service keeps the geometry in its own column.").format(value["from"], value["to"])
        return tr("The field “{}” is kept as “{}”: the service numbers its features itself.").format(value["from"], value["to"])
    if code == "curvesLinearized":
        return tr("Curved geometries are published as their linear approximation.")
    if code == "dimensionsDropped":
        dimensions = {
            "Z": tr("The Z values (altitude) of the geometries are not kept: the layer is published in 2D."),
            "M": tr("The M values (measure) of the geometries are not kept: the layer is published in 2D."),
            "ZM": tr("The Z values (altitude) and M values (measure) of the geometries are not kept: the layer is published in 2D."),
        }
        if value.get("dimensions") in dimensions:
            return dimensions[value["dimensions"]]
    if code == "projectionUnknown":
        return tr("The projection of this layer is unknown: choose it before publishing.")
    if code == "sourceKept":
        return tr("The file was kept: you can no longer remove it.")
    if code == "countsIncomplete":
        return tr("The publication finished after an interruption: the counts shown may be incomplete.")
    return html.escape(str(notice.get("message") or ""), quote=False)


def notice_texts(result):
    """The notices of a publication's result, said; its warnings from a server that sends none."""
    notices = result.get("notices") if isinstance(result, dict) else None
    if isinstance(notices, list):
        return [text for text in (notice_text(notice) for notice in notices) if text]
    warnings = result.get("warnings") if isinstance(result, dict) else None
    return [html.escape(str(warning), quote=False) for warning in (warnings or [])]
