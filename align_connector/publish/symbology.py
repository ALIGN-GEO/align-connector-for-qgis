# -*- coding: utf-8 -*-
"""The layer's symbology, carried to ALIGN — and what will not make it, said first.

A QGIS renderer is richer than any web style: hatched fills, SVG markers,
widths in map units, properties driven by expressions, nested rules. ALIGN
renders on the open MapLibre model, through its own five-mode symbology
(ADR-0010: single, categorized, graduated, rule-based, expression). This
module converts the former to the latter, and does one thing besides: every
simplification it has to make is **listed before the send**. A publisher reads
"hatched fill rendered flat" in the dialog, not on the map afterwards.

Written against the QGIS API by method NAMES (``renderer.type()``,
``symbolLayer.layerType()``, ``color.name()``…) rather than against classes,
so the decisions are proven on a plain interpreter with stand-ins
(``scripts/smoke_symbology.py``); QGIS itself is never imported here.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import math
import re
from contextlib import suppress

from ..i18n import tr
from . import svg_images

#: Paint family per geometry, the MapLibre vocabulary ALIGN keys its style on.
_FAMILY_BY_GEOMETRY = {0: "circle", 1: "line", 2: "fill", "point": "circle", "line": "line", "polygon": "fill"}

#: Pixels per unit at 96 dpi, the screen QGIS assumes for its millimetres.
_PX_PER_UNIT = {
    "mm": 96.0 / 25.4, "millimeters": 96.0 / 25.4,
    "point": 96.0 / 72.0, "points": 96.0 / 72.0,
    "pixel": 1.0, "pixels": 1.0,
    "inch": 96.0, "inches": 96.0,
}

#: The ALIGN outline block refuses a width beyond this (Style.ts).
_OUTLINE_MAX_WIDTH = 20.0

#: Symbol-layer types ALIGN renders faithfully; anything else is a flat stand-in.
_FAITHFUL_LAYER_TYPES = ("SimpleFill", "SimpleLine", "SimpleMarker")

_HEX6 = re.compile(r"^#[0-9a-f]{6}$")

#: ``"field" = 'value'`` or ``field = 12`` — the only rule filter ALIGN can turn
#: into a category without an expression engine on the other side.
_EQUALITY = re.compile(
    r"""^\s*"?(?P<field>[^"=<>!]+?)"?\s*=\s*(?:'(?P<text>[^']*)'|(?P<number>-?\d+(?:\.\d+)?))\s*$"""
)

#: QGIS classification method ids → the ALIGN scheme vocabulary (Style.ts).
_SCHEMES = {"EqualInterval": "equal-interval", "Quantile": "quantile", "Jenks": "natural-breaks"}

#: The symbol layer whose SVG ALIGN draws, per family (PH-37): a point's
#: marker, a polygon's pattern. Lines have none.
_SVG_LAYER_TYPES = {"circle": "SvgMarker", "fill": "SVGFill"}

#: Bounds of a published style's labels and marker (Style.ts, AssetLabels and AssetImages).
_LABEL_SIZE_PX = (4.0, 72.0)
_HALO_MAX_PX = 10.0
_MARKER_SIZE_PX = (4.0, 128.0)
_LABEL_FIELD_MAX = 256

#: QGIS label placements, by enum name (QGIS 4) and by value (QGIS 3).
_LINE_PLACEMENTS = ("Line", "Curved", "PerimeterCurved", 2, 3, 7)
_POINT_PLACEMENTS = ("AroundPoint", "OverPoint", "OrderedPositionsAroundPoint", 0, 1, 6)
_HORIZONTAL_PLACEMENTS = ("Horizontal", 4)

#: The map scale at web zoom 0, at the 96 dpi QGIS assumes for a screen: a
#: label's scale range becomes zoom levels, exact at the equator only.
_SCALE_AT_ZOOM_0 = 295829355.45

#: ``"field"`` or ``field``: a label expression that is only a field.
_FIELD_EXPRESSION = re.compile(r'^\s*(?:"(?P<quoted>[^"]+)"|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))\s*$')


class Conversion(object):
    """What the layer's symbology becomes, and what was lost on the way.

    :param style: the canonical ALIGN style to send, or ``None`` when the
        layer should simply take ALIGN's default
    :param summary: one short phrase for the dialog ("categorized on zone, 4 classes")
    :param report: the simplifications, one sentence each, in the order found
    """

    def __init__(self, style, summary, report):
        self.style = style
        self.summary = summary
        self.report = list(report)


def convert(layer):
    """Convert the layer's current renderer. Never raises on a style it does not
    know: that case is a reported degradation, not an error."""
    report = []
    family = _family_of(layer)
    if family is None:
        return Conversion(None, None, [tr("Unknown geometry type: the layer takes ALIGN's default style.")])

    renderer = layer.renderer()
    if renderer is None:
        return Conversion(None, None, [tr("No renderer: the layer takes ALIGN's default style.")])

    style, summary = _convert_renderer(renderer, family, report)
    labels = _labels(layer, family, report) if _labels_enabled(layer) else None
    images = _images(renderer, family, report)
    if style is None:
        if labels or images:
            report.append(tr("Labels and SVG images travel with a style: without one, ALIGN's default applies."))
        return Conversion(None, summary, report)
    parts = [summary]
    if images:
        style["images"] = images
        parts.append(tr("SVG marker") if "markerSvg" in images else tr("SVG pattern"))
    if labels:
        style["labels"] = labels
        parts.append(tr("labels on {}").format(labels["field"]))
    return Conversion(style, ", ".join(parts), report)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def _convert_renderer(renderer, family, report):
    kind = _call(renderer, "type", "")

    if kind == "singleSymbol":
        base, outline = _symbol_paint(_symbol_of(renderer), family, report, tr("the symbol"))
        return _style(family, base, outline, {"type": "simple"}), tr("single symbol")

    if kind == "categorizedSymbol":
        return _categorized(renderer, family, report)

    if kind == "graduatedSymbol":
        return _graduated(renderer, family, report)

    if kind == "RuleRenderer":
        return _rule_based(renderer, family, report)

    if kind == "heatmapRenderer":
        radius = _px(_call(renderer, "radius", 10.0), _call(renderer, "radiusUnit", "mm"), report, tr("the heatmap radius"))
        report.append(tr("Heatmap: the web engine's own colour ramp applies."))
        return {"renderType": "heatmap", "paint": {"heatmap-radius": radius}}, tr("heatmap")

    if kind == "pointCluster":
        inner_style, summary = _convert_renderer(_call(renderer, "embeddedRenderer"), family, report)
        if inner_style is None:
            return None, summary
        tolerance = _px(_call(renderer, "tolerance", 3.0), _call(renderer, "toleranceUnit", "mm"), report, tr("the cluster distance"))
        inner_style["cluster"] = {"enabled": True, "radius": max(1.0, tolerance)}
        return inner_style, tr("{} (clustered)").format(summary)

    if kind == "pointDisplacement":
        report.append(tr("Point displacement is rendered as plain points."))
        return _convert_renderer(_call(renderer, "embeddedRenderer"), family, report)

    if kind == "nullSymbol":
        report.append(tr("No symbol in QGIS (the layer is invisible there): it takes ALIGN's default style."))
        return None, None

    report.append(tr("Renderer '{}' has no web equivalent: the layer takes ALIGN's default style.").format(kind or tr("unknown")))
    return None, None


def _categorized(renderer, family, report):
    attribute = (_call(renderer, "classAttribute", "") or "").strip()
    key = _color_key(family)
    rules = []
    fallback = None
    for category in _call(renderer, "categories", []) or []:
        if not _call(category, "renderState", True):
            report.append(tr("Hidden category '{}' is not transmitted.").format(_call(category, "label", "")))
            continue
        base, outline = _symbol_paint(_symbol_of(category), family, report, tr("category '{}'").format(_call(category, "label", "")))
        value = _call(category, "value", None)
        if value is None or value == "":
            # QGIS's "all other values" row: the colour everything else takes.
            fallback = (base, outline)
            continue
        label = _call(category, "label", "") or ""
        for one in (value if isinstance(value, (list, tuple)) else [value]):
            rule = {"value": one, "paint": {key: base[key]}}
            if label:
                rule["label"] = label
            rules.append(rule)
    if not attribute or not rules:
        report.append(tr("Categorized renderer without usable categories: the first symbol applies to everything."))
        first = _first_symbol(renderer)
        base, outline = _symbol_paint(first, family, report, tr("the symbol"))
        return _style(family, base, outline, {"type": "simple"}), tr("single symbol")
    if fallback is None:
        fallback = _neutral_fallback(_first_symbol(renderer), family, report, tr("the first category"))
    base, outline = fallback
    symbology = {"type": "categorized", "attribute": attribute, "rules": rules}
    return _style(family, base, outline, symbology), tr("categorized on {}, {} classes").format(attribute, len(rules))


def _graduated(renderer, family, report):
    attribute = (_call(renderer, "classAttribute", "") or "").strip()
    key = _color_key(family)
    rules = []
    for rng in _call(renderer, "ranges", []) or []:
        if not _call(rng, "renderState", True):
            continue
        base, outline = _symbol_paint(_symbol_of(rng), family, report, tr("class '{}'").format(_call(rng, "label", "")))
        lower, upper = _call(rng, "lowerValue", None), _call(rng, "upperValue", None)
        if not isinstance(lower, (int, float)) or not isinstance(upper, (int, float)) or upper <= lower:
            continue
        rule = {"range": [float(lower), float(upper)], "paint": {key: base[key]}}
        label = _call(rng, "label", "") or ""
        if label:
            rule["label"] = label
        rules.append(rule)
    if not attribute or not rules:
        report.append(tr("Graduated renderer without usable classes: the first symbol applies to everything."))
        base, outline = _symbol_paint(_first_symbol(renderer), family, report, tr("the symbol"))
        return _style(family, base, outline, {"type": "simple"}), tr("single symbol")
    if _is_size_method(_call(renderer, "graduatedMethod", None)):
        report.append(tr("Graduated by size: the classes keep their colours, the sizes do not vary on the web."))
    rules.sort(key=lambda r: r["range"][0])
    ranges = _call(renderer, "ranges", []) or []
    base, outline = _symbol_paint(_symbol_of(ranges[0]) if ranges else None, family, report, tr("the first class"))
    symbology = {"type": "graduated", "attribute": attribute, "interpolation": "step", "classCount": len(rules), "rules": rules}
    method = _call(_call(renderer, "classificationMethod", None), "id", None)
    if method in _SCHEMES:
        symbology["classificationScheme"] = _SCHEMES[method]
    return _style(family, base, outline, symbology), tr("graduated on {}, {} classes").format(attribute, len(rules))


def _rule_based(renderer, family, report):
    """A rule tree is an expression language; ALIGN reads one case of it: flat
    equality rules on one field (with an optional ELSE), which is a category."""
    root = _call(renderer, "rootRule")
    children = [r for r in (_call(root, "children", []) or []) if _call(r, "active", True)]
    key = _color_key(family)
    field = None
    rules = []
    fallback = None
    convertible = bool(children)
    for rule in children:
        if _call(rule, "children", []):
            convertible = False
            break
        symbol = _symbol_of(rule)
        if symbol is None:
            convertible = False
            break
        base, outline = _symbol_paint(symbol, family, report, tr("rule '{}'").format(_call(rule, "label", "")))
        if _call(rule, "isElse", False):
            fallback = (base, outline)
            continue
        match = _EQUALITY.match(_call(rule, "filterExpression", "") or "")
        if not match:
            convertible = False
            break
        this_field = match.group("field").strip()
        if field is None:
            field = this_field
        elif this_field != field:
            convertible = False
            break
        value = match.group("text") if match.group("text") is not None else _number(match.group("number"))
        entry = {"value": value, "paint": {key: base[key]}}
        label = _call(rule, "label", "") or ""
        if label:
            entry["label"] = label
        rules.append(entry)
    if convertible and field and rules:
        if fallback is None:
            fallback = _neutral_fallback(_symbol_of(children[0]), family, report, tr("the first rule"))
        base, outline = fallback
        report.append(tr("Rule-based renderer transmitted as categories on '{}'.").format(field))
        return _style(family, base, outline, {"type": "categorized", "attribute": field, "rules": rules}), \
            tr("categorized on {}, {} classes").format(field, len(rules))
    report.append(tr("Rule-based renderer with expressions ALIGN cannot read: the first rule's symbol applies to everything."))
    first = _first_symbol(renderer)
    if first is None:
        return None, None
    base, outline = _symbol_paint(first, family, report, tr("the first rule"))
    return _style(family, base, outline, {"type": "simple"}), tr("single symbol")


#: What a feature outside every category or rule looks like on ALIGN when the
#: renderer has no "all other values" row (TCK-2220): a light, translucent
#: grey that belongs to no class. QGIS hides such a feature; the viewer's
#: engine draws it with the layer's base paint, so that paint must not be a
#: class's colour, or the feature passes for a member of that class.
NEUTRAL_FALLBACK = ("#cbd5e1", 0.4)


def _neutral_fallback(first_symbol, family, report, what):
    """The base paint when nothing says what an unclassified feature looks
    like: neutral grey, with the first class's outline (the outline is the
    layer's, shared by every class, and the first class is as good a source
    as any). Says so in the report."""
    _, outline = _symbol_paint(first_symbol, family, report, what)
    colour, opacity = NEUTRAL_FALLBACK
    report.append(tr("Features outside every category are drawn in neutral grey (QGIS would hide them)."))
    return {_color_key(family): colour, _opacity_key(family): opacity}, outline


# ---------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------

def _symbol_paint(symbol, family, report, what):
    """The MapLibre paint of one QGIS symbol for the family, plus the polygon
    outline block. Reads the bottom symbol layer; reports what it had to drop."""
    key = _color_key(family)
    opacity_key = _opacity_key(family)
    if symbol is None:
        return {key: "#3b82f6", opacity_key: 1.0}, None

    layers = list(_call(symbol, "symbolLayers", []) or [])
    layer = _pick_layer(layers)
    if len(layers) > 1:
        report.append(tr("{}: {} symbol layers, only the {} one is kept.").format(
            _cap(what), len(layers), tr("simple") if layer is not layers[0] else tr("bottom")))
    layer_type = _call(layer, "layerType", "") if layer is not None else ""
    # An SVG marker or pattern travels as an image; `_images` says so when it cannot.
    if layer is not None and layer_type not in _FAITHFUL_LAYER_TYPES and layer_type != _SVG_LAYER_TYPES.get(family):
        report.append(tr("{}: '{}' has no web equivalent, rendered as a flat {}.").format(
            _cap(what), layer_type or tr("unknown symbol layer"), {"fill": tr("fill"), "line": tr("line"), "circle": tr("circle")}[family]))
    if layer is not None and _data_defined(layer):
        report.append(tr("{}: properties driven by expressions are ignored.").format(_cap(what)))

    color = _color_of(layer, symbol, family, report, what)
    symbol_opacity = _call(symbol, "opacity", 1.0)
    opacity = round(float(symbol_opacity) * color[1], 3)
    base = {key: color[0], opacity_key: opacity}
    outline = None

    if family == "fill":
        if layer is not None and layer_type == "SimpleFill" and _call(layer, "brushStyle", 1) == 0:
            base[opacity_key] = 0.0
            report.append(tr("{}: no brush, the polygon is transparent.").format(_cap(what)))
        outline = _outline_of(layer, report, what)
    elif family == "line":
        width = _px(_call(layer, "width", 0.26), _call(layer, "widthUnit", "mm"), report, tr("{} width").format(what))
        base["line-width"] = round(max(width, 0.5), 2)
        if layer is not None and _call(layer, "penStyle", 1) not in (1, None):
            report.append(tr("{}: dash pattern not transmitted, solid line.").format(_cap(what)))
    else:
        size = _px(_call(layer, "size", 2.0), _call(layer, "sizeUnit", "mm"), report, tr("{} size").format(what))
        base["circle-radius"] = round(max(size / 2.0, 1.0), 2)
        if layer_type == "SimpleMarker" and not _is_circle(_call(layer, "shape", None)):
            report.append(tr("{}: marker shape rendered as a circle.").format(_cap(what)))
        stroke = _hex(_call(layer, "strokeColor", None))
        if stroke:
            base["circle-stroke-color"] = stroke[0]
            base["circle-stroke-width"] = round(_px(_call(layer, "strokeWidth", 0.0), _call(layer, "strokeWidthUnit", "mm"), report, tr("{} stroke").format(what)), 2)
    return base, outline


def _outline_of(layer, report, what):
    if layer is None or _call(layer, "strokeStyle", 1) == 0:
        return None
    stroke = _hex(_call(layer, "strokeColor", None))
    width = _px(_call(layer, "strokeWidth", 0.26), _call(layer, "strokeWidthUnit", "mm"), report, tr("{} outline").format(what))
    if width <= 0:
        return None
    if width > _OUTLINE_MAX_WIDTH:
        report.append(tr("{}: outline width reduced to {} px (ALIGN's maximum).").format(_cap(what), int(_OUTLINE_MAX_WIDTH)))
        width = _OUTLINE_MAX_WIDTH
    outline = {"enabled": True, "width": round(width, 2)}
    if stroke:
        outline["color"] = stroke[0]
        if stroke[1] < 1.0:
            outline["opacity"] = round(stroke[1], 3)
    return outline


def _color_of(layer, symbol, family, report, what):
    """``(hex, alpha)`` of the symbol's colour: the layer's fill for a polygon
    or marker, the stroke for a line; the symbol's own colour as the fallback."""
    candidates = []
    if layer is not None:
        if family == "fill":
            candidates.append(_call(layer, "fillColor", None))
        candidates.append(_call(layer, "color", None))
    candidates.append(_call(symbol, "color", None))
    for candidate in candidates:
        parsed = _hex(candidate)
        if parsed:
            return parsed
    report.append(tr("{}: no readable colour, ALIGN's default blue applies.").format(_cap(what)))
    return "#3b82f6", 1.0


def _pick_layer(layers):
    """The symbol layer whose look is sent: the first faithful one, else the bottom one."""
    for layer in layers:
        if _call(layer, "layerType", "") in _FAITHFUL_LAYER_TYPES:
            return layer
    return layers[0] if layers else None


def _first_symbol(renderer):
    """The symbol a degraded renderer falls back to: its first class, rule or
    symbol, whichever the renderer kind exposes."""
    for accessor in ("categories", "ranges"):
        items = _call(renderer, accessor, None)
        if items:
            symbol = _symbol_of(items[0])
            if symbol is not None:
                return symbol
    children = _call(_call(renderer, "rootRule"), "children", None)
    if children:
        symbol = _symbol_of(children[0])
        if symbol is not None:
            return symbol
    # No render context available means the plain accessor is next, whatever
    # the binding raised: the same reading as the typed fallbacks above.
    with suppress(Exception):
        from qgis.core import QgsRenderContext

        symbols = _call(renderer, "symbols", None, QgsRenderContext())
        if symbols:
            return _own(symbols[0])
    return _symbol_of(renderer)


def _symbol_of(holder):
    """The symbol a renderer, a category, a range or a rule carries, OWNED.

    QGIS hands categories and ranges back as temporary copies, each owning
    its symbol; the pointer `symbol()` returns dies with the copy, and
    reading it afterwards is a crash with no traceback (seen on 3.44 with a
    graduated renderer). A clone belongs to Python and outlives the copy.
    """
    return _own(_call(holder, "symbol"))


def _own(symbol):
    clone = getattr(symbol, "clone", None)
    if not callable(clone):
        return symbol
    try:
        return clone()
    except Exception:  # noqa: BLE001 — a stand-in without a clone is used as it is
        return symbol


def _is_size_method(method):
    """QGIS 3.x says ``1`` (GraduatedSize), QGIS 4 says ``Qgis.GraduatedMethod.Size``."""
    return method == 1 or getattr(method, "name", None) == "Size"


def _is_circle(shape):
    """``None`` (no shape accessor), ``8`` (QGIS 3.x Circle) or the enum member named Circle."""
    return shape is None or shape == 8 or getattr(shape, "name", None) == "Circle"


# ---------------------------------------------------------------------------
# Labels (PH-37)
# ---------------------------------------------------------------------------

def _labels(layer, family, report):
    """The layer's simple labelling as ALIGN's ``labels`` block, or ``None``.

    ALIGN labels a feature with one field, in one size, colour and halo, on a
    point or along a line, between two zooms. Whatever QGIS adds to that is
    named in the report.
    """
    labeling = _call(layer, "labeling", None)
    kind = _call(labeling, "type", "")
    if kind == "rule-based":
        report.append(tr("Labels: rule-based labelling is not transmitted; add the labels in the Map builder."))
        return None
    settings = _call(labeling, "settings", None)
    if kind != "simple" or settings is None:
        report.append(tr("Labels: this labelling cannot be read, so it is not transmitted."))
        return None
    if not _attr(settings, "drawLabels", True):
        return None

    field = _label_field(settings)
    if not field:
        report.append(tr("Labels: the expression '{}' is not transmitted, only a field is; add the labels in the Map builder.").format(
            _attr(settings, "fieldName", "") or ""))
        return None

    text_format = _call(settings, "format", None)
    size = _px(_call(text_format, "size", 10.0), _call(text_format, "sizeUnit", "Point"), report, tr("the label size"))
    labels = {"field": field, "size": _bounded(size, _LABEL_SIZE_PX, report, tr("Labels: size"))}
    color = _hex(_call(text_format, "color", None))
    if color:
        labels["color"] = color[0]
    buffer = _call(text_format, "buffer", None)
    if buffer is not None and _call(buffer, "enabled", False):
        halo = _hex(_call(buffer, "color", None))
        if halo:
            labels["haloColor"] = halo[0]
        width = _px(_call(buffer, "size", 1.0), _call(buffer, "sizeUnit", "mm"), report, tr("the label halo"))
        labels["haloWidth"] = _bounded(width, (0.0, _HALO_MAX_PX), report, tr("Labels: halo"))
    labels["placement"] = _placement(_attr(settings, "placement", None), family, report)
    labels.update(_zoom_range(settings, report))

    font = _call(_call(text_format, "font", None), "family", "")
    if font:
        report.append(tr("Labels: drawn in the map's font, not '{}'.").format(font))
    if _call(_call(text_format, "background", None), "enabled", False):
        report.append(tr("Labels: the background shape is not transmitted."))
    if _call(_call(text_format, "shadow", None), "enabled", False):
        report.append(tr("Labels: the shadow is not transmitted."))
    properties = _call(settings, "dataDefinedProperties", None)
    if properties is not None and _call(properties, "hasActiveProperties", False):
        report.append(tr("Labels: properties driven by expressions are ignored."))
    return labels


def _label_field(settings):
    """The field the labels read: the field itself, or an expression that is only a field."""
    name = (_attr(settings, "fieldName", "") or "").strip()
    if _attr(settings, "isExpression", False):
        match = _FIELD_EXPRESSION.match(name)
        name = (match.group("quoted") or match.group("bare")) if match else ""
    return name if 0 < len(name) <= _LABEL_FIELD_MAX else None


def _placement(placement, family, report):
    """``line`` along a line, ``point`` otherwise; a QGIS placement ALIGN draws differently is reported."""
    names = (getattr(placement, "name", None), placement)
    if family == "line" and any(name in _LINE_PLACEMENTS for name in names):
        return "line"
    expected = _POINT_PLACEMENTS + _HORIZONTAL_PLACEMENTS if family != "line" else _HORIZONTAL_PLACEMENTS
    if placement is not None and not any(name in expected for name in names):
        report.append(tr("Labels: the '{}' placement is drawn as a label on the feature's point.").format(
            getattr(placement, "name", None) or placement))
    return "point"


def _zoom_range(settings, report):
    """The labels' scale range as web zoom levels: ``minZoom`` from the most
    zoomed-out scale, ``maxZoom`` from the most zoomed-in one; 0 is no limit."""
    if not _attr(settings, "scaleVisibility", False):
        return {}
    zooms = {}
    for key, attribute in (("minZoom", "minimumScale"), ("maxZoom", "maximumScale")):
        try:
            scale = float(_attr(settings, attribute, 0) or 0)
        except (TypeError, ValueError):
            scale = 0.0
        if scale > 0:
            zooms[key] = round(min(max(math.log2(_SCALE_AT_ZOOM_0 / scale), 0.0), 24.0), 1)
    if zooms:
        report.append(tr("Labels: the scale range becomes map zoom levels, exact at the equator only."))
    return zooms


# ---------------------------------------------------------------------------
# SVG images (PH-37)
# ---------------------------------------------------------------------------

def _images(renderer, family, report):
    """The SVG marker of a point layer or the SVG pattern of a polygon layer,
    as ALIGN's ``images`` block, or ``None``. One image per layer: the classes
    of a categorized marker share it, each in its own colour."""
    wanted = _SVG_LAYER_TYPES.get(family)
    if wanted is None:
        return None
    # The symbols are clones that own their layers: they must outlive every
    # read of a layer below, or QGIS frees the layers under us (a black marker
    # read from a freed SvgMarker, and a crash waiting to happen).
    symbols = _symbols_of(renderer)
    layers = [layer for symbol in symbols for layer in (_call(symbol, "symbolLayers", []) or [])
              if _call(layer, "layerType", "") == wanted]
    if not layers:
        return None
    plain = tr("drawn as a plain circle") if family == "circle" else tr("drawn as a plain fill")
    paths = {_svg_path(layer) for layer in layers}
    if len(paths) > 1:
        report.append(tr("SVG: the classes draw different images, and ALIGN draws one per layer; {}.").format(plain))
        return None
    layer = layers[0]
    found = svg_images.read(_svg_path(layer), _svg_search_paths())
    if found.markup is None:
        report.append(tr("SVG: {}; {}.").format(found.problem, plain))
        return None
    images = _marker(layer, found.markup, report) if family == "circle" else _pattern(layer, found.markup, report)
    markup = images.get("markerSvg") or images.get("fillPatternSvg")
    if len(markup) > svg_images.MAX_CHARS:
        report.append(tr("SVG: the image weighs more than ALIGN's limit of 256 KB; {}.").format(plain))
        return None
    return images


def _marker(layer, markup, report):
    """An SVG marker: its outline as QGIS sets it, written into the markup;
    its fill left to ALIGN, which colours the marker per class."""
    size = _px(_call(layer, "size", 2.0), _call(layer, "sizeUnit", "mm"), report, tr("the SVG marker size"))
    size = _bounded(size, _MARKER_SIZE_PX, report, tr("SVG: marker size"))
    images = {"markerSvg": svg_images.with_parameters(markup, _outline_parameters(layer, markup, size, "", report)), "markerSize": size}
    fill = _hex(_call(layer, "fillColor", None))
    if fill:
        images["markerColor"] = fill[0]
        images["markerSvg"] = svg_images.with_parameters(images["markerSvg"], {"fill-opacity": _number_text(fill[1])})
    return images


def _pattern(layer, markup, report):
    """An SVG fill: every parameter written, the tile drawn at QGIS's width."""
    width = _px(_call(layer, "patternWidth", 20.0), _call(layer, "patternWidthUnit", "mm"), report, tr("the SVG pattern width"))
    width = max(width, 2.0)
    values = _outline_parameters(layer, markup, width, "svg", report)
    fill = _hex(_call(layer, "svgFillColor", None))
    if fill:
        values.update({"fill": fill[0], "fill-opacity": _number_text(fill[1])})
    return {"fillPatternSvg": svg_images.sized(svg_images.with_parameters(markup, values), width)}


def _outline_parameters(layer, markup, drawn_px, prefix, report):
    """QGIS's outline parameters for an SVG drawn ``drawn_px`` wide. The width
    is in the SVG's own units, as QGIS writes it: pixels × viewBox ÷ drawn size."""
    getter = (lambda name: prefix + name[0].upper() + name[1:]) if prefix else (lambda name: name)
    values = {}
    stroke = _hex(_call(layer, getter("strokeColor"), None))
    if stroke:
        values.update({"outline": stroke[0], "outline-opacity": _number_text(stroke[1])})
    width_px = _px(_call(layer, getter("strokeWidth"), 0.0), _call(layer, getter("strokeWidthUnit"), "mm"), report, tr("the SVG outline"))
    view = svg_images.view_box_width(markup)
    values["outline-width"] = _number_text(width_px * view / drawn_px if view else width_px)
    return values


def _svg_path(layer):
    return (_call(layer, "path", None) or _call(layer, "svgFilePath", None) or "").strip()


def _svg_search_paths():
    """QGIS's SVG library folders, where a symbol may name its file without a path."""
    with suppress(Exception):
        from qgis.core import QgsApplication

        return [str(path) for path in QgsApplication.svgPaths()]
    return []


def _symbols_of(renderer):
    """Every symbol the renderer draws with: its symbol, or its visible classes and rules."""
    kind = _call(renderer, "type", "")
    if kind in ("pointCluster", "pointDisplacement"):
        return _symbols_of(_call(renderer, "embeddedRenderer", None))
    holders = []
    if kind == "singleSymbol":
        holders = [renderer]
    elif kind == "categorizedSymbol":
        holders = [c for c in (_call(renderer, "categories", []) or []) if _call(c, "renderState", True)]
    elif kind == "graduatedSymbol":
        holders = [r for r in (_call(renderer, "ranges", []) or []) if _call(r, "renderState", True)]
    elif kind == "RuleRenderer":
        holders = [r for r in (_call(_call(renderer, "rootRule", None), "children", []) or []) if _call(r, "active", True)]
    return [symbol for symbol in (_symbol_of(holder) for holder in holders) if symbol is not None]


def _bounded(value, bounds, report, what):
    """``value`` within ``bounds``, reported when it had to move."""
    low, high = bounds
    bounded = round(min(max(value, low), high), 2)
    if bounded != round(value, 2):
        report.append(tr("{}: {} px, within ALIGN's range ({} to {} px).").format(what, bounded, int(low), int(high)))
    return bounded


def _number_text(value):
    """A number as an SVG attribute writes it: short, no trailing zeros."""
    return "{:.4f}".format(float(value)).rstrip("0").rstrip(".") or "0"


# ---------------------------------------------------------------------------
# Readers, pure and defensive
# ---------------------------------------------------------------------------

def _attr(target, name, default=None):
    """``target.name``, called when it is a method: QgsPalLayerSettings holds
    plain attributes where the rest of the API has accessors."""
    if target is None:
        return default
    try:
        value = getattr(target, name)
    except AttributeError:
        return default
    if callable(value):
        try:
            return value()
        except Exception:  # noqa: BLE001 — same contract as `_call`
            return default
    return value

def _style(family, base, outline, symbology):
    style = {"renderType": family, "paint": base, "symbology": symbology}
    if outline:
        style["outline"] = outline
    return style


def _color_key(family):
    return {"fill": "fill-color", "line": "line-color", "circle": "circle-color"}[family]


def _opacity_key(family):
    return {"fill": "fill-opacity", "line": "line-opacity", "circle": "circle-opacity"}[family]


def _family_of(layer):
    geometry = _call(layer, "geometryType", None)
    name = getattr(geometry, "name", None)
    if isinstance(name, str):
        return _FAMILY_BY_GEOMETRY.get(name.lower())
    value = getattr(geometry, "value", geometry)
    try:
        return _FAMILY_BY_GEOMETRY.get(int(value))
    except (TypeError, ValueError):
        return None


def _labels_enabled(layer):
    return bool(_call(layer, "labelsEnabled", False)) and _call(layer, "labeling", None) is not None


def _data_defined(layer):
    properties = _call(layer, "dataDefinedProperties", None)
    return bool(_call(properties, "hasActiveProperties", False)) if properties is not None else False


def _hex(color):
    """``(hex, alpha)`` of a QColor-like, or ``None`` when there is no colour."""
    if color is None:
        return None
    name = _call(color, "name", None)
    if not isinstance(name, str):
        return None
    name = name.strip().lower()
    if not _HEX6.match(name):
        return None
    alpha = _call(color, "alphaF", 1.0)
    try:
        alpha = min(max(float(alpha), 0.0), 1.0)
    except (TypeError, ValueError):
        alpha = 1.0
    return name, alpha


def _px(value, unit, report, what):
    """A size in CSS pixels. Map units and metres have no screen size: they
    are reported and rendered at one millimetre."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        amount = 0.0
    factor = _PX_PER_UNIT.get(_unit_name(unit))
    if factor is None:
        report.append(tr("{}: map-unit size has no screen equivalent, rendered at 1 mm.").format(_cap(what)))
        factor = _PX_PER_UNIT["mm"]
        amount = min(amount, 1.0) if amount > 0 else 1.0
    return round(amount * factor, 2)


def _unit_name(unit):
    if isinstance(unit, str):
        return unit.strip().lower()
    name = getattr(unit, "name", None)
    if isinstance(name, str):
        return name.lower()
    # Any binding gap means "unknown unit", which the caller reports.
    with suppress(Exception):
        from qgis.core import QgsUnitTypes

        encoded = QgsUnitTypes.encodeUnit(unit)
        if isinstance(encoded, str):
            return encoded.lower()
    return "unknown"


def _number(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        try:
            return float(text)
        except (TypeError, ValueError):
            return text


def _call(target, name, default=None, *args):
    """``target.name(*args)``, or ``default`` when the method is missing or raises.

    The QGIS API varies across the versions this plugin supports; a missing
    accessor is a degradation to report, never a crash in the publish dialog.
    """
    if target is None:
        return default
    method = getattr(target, name, None)
    if not callable(method):
        return default
    try:
        return method(*args)
    except Exception:  # noqa: BLE001 — see docstring
        return default


def _cap(text):
    return text[:1].upper() + text[1:] if text else text
