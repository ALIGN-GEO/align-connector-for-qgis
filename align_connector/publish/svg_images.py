# -*- coding: utf-8 -*-
"""The SVG files of a QGIS symbol, made into what ALIGN keeps (PH-37).

QGIS draws an SVG marker or an SVG fill from a FILE: a path on this machine,
a name in QGIS's own SVG library, or the image embedded in the project
(``base64:``). ALIGN keeps an image only as SVG text inside the style, and
only when that text is self-contained: no script, no event handler, no link
to another file. This module reads the file, says why it cannot travel when
it cannot, and writes QGIS's colour parameters (``param(fill) #000000``) the
way the symbol sets them.

The rule that decides is ALIGN's (``isDrawableStyleImage``,
``map-viewer/packages/align-authoring/src/align-style-image.ts``): the server
leaves out an image that breaks it, and the publish goes on. The check here
mirrors it only to say so BEFORE the send; a drift would change what the
report announces, never what ALIGN accepts.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import base64
import binascii
import os
import re
from contextlib import suppress

from ..i18n import tr

#: ALIGN's cap on one published image (``Style.ts``, ``STYLE_IMAGE_MAX_LENGTH``):
#: two at the cap still fit in the publish request under the API's 1 MiB.
MAX_CHARS = 256 * 1024

_EXTERNAL_REFERENCE = r"""\s*=\s*(?:"(?!\s*#)|'(?!\s*#)|(?!["'#\s]))"""

#: The mirror of ALIGN's ``SVG_FORBIDDEN``: what makes an SVG not self-contained.
_NOT_SELF_CONTAINED = re.compile(
    r"<script|<foreignObject|<iframe|<object|<embed|\son[a-z]+\s*=|javascript:"
    r"|xlink:href" + _EXTERNAL_REFERENCE
    + r"|\shref" + _EXTERNAL_REFERENCE
    + r"|<use\b[^>]*href" + _EXTERNAL_REFERENCE
    + r"|<!ENTITY",
    re.IGNORECASE,
)

_SVG_ROOT = re.compile(r"<svg\b[^>]*>", re.IGNORECASE)
_VIEW_BOX = re.compile(r"""\bviewBox\s*=\s*["']\s*[-\d.eE+]+[\s,]+[-\d.eE+]+[\s,]+([\d.eE+]+)[\s,]+([\d.eE+]+)""", re.IGNORECASE)
_SIZE_ATTRIBUTE = re.compile(r"""\s(width|height)\s*=\s*(["'])[^"']*\2""", re.IGNORECASE)


class SvgFile(object):
    """The text of an SVG a symbol draws, or why it cannot travel.

    :param markup: the SVG text, or ``None``
    :param problem: one sentence for the report when ``markup`` is ``None``
    """

    def __init__(self, markup, problem=None):
        self.markup = markup
        self.problem = problem


def read(path, search_paths=()):
    """Read the SVG a symbol layer names, as QGIS finds it.

    :param path: the layer's path: ``base64:…``, a file, or a name in one of
        ``search_paths`` (QGIS's SVG library)
    """
    path = (path or "").strip()
    name = os.path.basename(path) or path
    if not path:
        return SvgFile(None, tr("the symbol names no SVG file"))
    if path.lower().startswith("base64:"):
        try:
            text = base64.b64decode(path[len("base64:"):], validate=False).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return SvgFile(None, tr("the SVG embedded in the project cannot be read"))
        return _checked(text, tr("the SVG embedded in the project"))
    if re.match(r"^[a-z][a-z0-9+.-]*://", path, re.IGNORECASE):
        return SvgFile(None, tr("the SVG '{}' is on the web; ALIGN keeps only an SVG written into the style").format(name))
    for candidate in [path] + [os.path.join(root, path) for root in search_paths if root]:
        if os.path.isfile(candidate):
            text = None
            with suppress(OSError, UnicodeDecodeError):
                with open(candidate, "r", encoding="utf-8-sig") as handle:
                    text = handle.read(MAX_CHARS * 4 + 1)
            if text is None:
                return SvgFile(None, tr("the SVG file '{}' cannot be read").format(name))
            return _checked(text, tr("the SVG '{}'").format(name))
    return SvgFile(None, tr("the SVG file '{}' is not found on this computer").format(name))


def _checked(text, what):
    markup = text.strip()
    if not markup.startswith("<") or not re.search(r"<svg[\s>]", markup, re.IGNORECASE):
        return SvgFile(None, tr("{} is not SVG markup").format(what))
    if _NOT_SELF_CONTAINED.search(markup):
        return SvgFile(None, tr("{} runs a script or links to another file; ALIGN keeps only a self-contained SVG").format(what))
    return SvgFile(markup)


def with_parameters(markup, values):
    """The markup with QGIS's parameters written, each with the default QGIS
    writes after it (``param(fill) #000000``) replaced along with it.

    :param values: ``{"fill": "#d62828", "outline-width": "0.5", …}``; a
        parameter not given is left for ALIGN's engine
    """
    for name, value in values.items():
        markup = re.sub(r"param\(" + re.escape(name) + r"\)(?:\s+[^\s\"';>]+)?", value, markup, flags=re.IGNORECASE)
    return markup


def view_box_width(markup):
    """The width of the SVG's own coordinates (its viewBox, else its width), or None."""
    root = _SVG_ROOT.search(markup)
    if not root:
        return None
    match = _VIEW_BOX.search(root.group(0))
    if match:
        return _positive(match.group(1))
    width = re.search(r"""\swidth\s*=\s*["']\s*([\d.]+)""", root.group(0), re.IGNORECASE)
    return _positive(width.group(1)) if width else None


def sized(markup, width_px):
    """The markup drawn ``width_px`` wide, its height kept in proportion: the
    size of one tile of a fill pattern, which ALIGN draws at the SVG's own size."""
    root = _SVG_ROOT.search(markup)
    if not root:
        return markup
    tag = root.group(0)
    view = _VIEW_BOX.search(tag)
    ratio = (_positive(view.group(2)) or 1.0) / (_positive(view.group(1)) or 1.0) if view else 1.0
    stripped = _SIZE_ATTRIBUTE.sub("", tag)
    size = ' width="{}" height="{}"'.format(_round(width_px), _round(width_px * ratio))
    rewritten = re.sub(r"^<svg\b", "<svg" + size, stripped, count=1, flags=re.IGNORECASE)
    return markup[: root.start()] + rewritten + markup[root.end():]


def _positive(text):
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _round(value):
    rounded = round(float(value), 2)
    return int(rounded) if rounded == int(rounded) else rounded
