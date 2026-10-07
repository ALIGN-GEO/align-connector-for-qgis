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
import re

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
        return Conversion(None, None, ["Unknown geometry type: the layer takes ALIGN's default style."])

    renderer = layer.renderer()
    if renderer is None:
        return Conversion(None, None, ["No renderer: the layer takes ALIGN's default style."])

    if _labels_enabled(layer):
        report.append("Labels are not transmitted yet; add them in the Map builder.")

    style, summary = _convert_renderer(renderer, family, report)
    return Conversion(style, summary, report)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def _convert_renderer(renderer, family, report):
    kind = _call(renderer, "type", "")

    if kind == "singleSymbol":
        base, outline = _symbol_paint(_call(renderer, "symbol"), family, report, "the symbol")
        return _style(family, base, outline, {"type": "simple"}), "single symbol"

    if kind == "categorizedSymbol":
        return _categorized(renderer, family, report)

    if kind == "graduatedSymbol":
        return _graduated(renderer, family, report)

    if kind == "RuleRenderer":
        return _rule_based(renderer, family, report)

    if kind == "heatmapRenderer":
        radius = _px(_call(renderer, "radius", 10.0), _call(renderer, "radiusUnit", "mm"), report, "the heatmap radius")
        report.append("Heatmap: the web engine's own colour ramp applies.")
        return {"renderType": "heatmap", "paint": {"heatmap-radius": radius}}, "heatmap"

    if kind == "pointCluster":
        inner_style, summary = _convert_renderer(_call(renderer, "embeddedRenderer"), family, report)
        if inner_style is None:
            return None, summary
        tolerance = _px(_call(renderer, "tolerance", 3.0), _call(renderer, "toleranceUnit", "mm"), report, "the cluster distance")
        inner_style["cluster"] = {"enabled": True, "radius": max(1.0, tolerance)}
        return inner_style, "{} (clustered)".format(summary)

    if kind == "pointDisplacement":
        report.append("Point displacement is rendered as plain points.")
        return _convert_renderer(_call(renderer, "embeddedRenderer"), family, report)

    if kind == "nullSymbol":
        report.append("No symbol in QGIS (the layer is invisible there): it takes ALIGN's default style.")
        return None, None

    report.append("Renderer '{}' has no web equivalent: the layer takes ALIGN's default style.".format(kind or "unknown"))
    return None, None


def _categorized(renderer, family, report):
    attribute = (_call(renderer, "classAttribute", "") or "").strip()
    key = _color_key(family)
    rules = []
    fallback = None
    for category in _call(renderer, "categories", []) or []:
        if not _call(category, "renderState", True):
            report.append("Hidden category '{}' is not transmitted.".format(_call(category, "label", "")))
            continue
        base, outline = _symbol_paint(_call(category, "symbol"), family, report, "category '{}'".format(_call(category, "label", "")))
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
        report.append("Categorized renderer without usable categories: the first symbol applies to everything.")
        first = _first_symbol(renderer)
        base, outline = _symbol_paint(first, family, report, "the symbol")
        return _style(family, base, outline, {"type": "simple"}), "single symbol"
    if fallback is None:
        fallback = _symbol_paint(_first_symbol(renderer), family, report, "the first category")
    base, outline = fallback
    symbology = {"type": "categorized", "attribute": attribute, "rules": rules}
    return _style(family, base, outline, symbology), "categorized on {}, {} classes".format(attribute, len(rules))


def _graduated(renderer, family, report):
    attribute = (_call(renderer, "classAttribute", "") or "").strip()
    key = _color_key(family)
    rules = []
    for rng in _call(renderer, "ranges", []) or []:
        if not _call(rng, "renderState", True):
            continue
        base, outline = _symbol_paint(_call(rng, "symbol"), family, report, "class '{}'".format(_call(rng, "label", "")))
        lower, upper = _call(rng, "lowerValue", None), _call(rng, "upperValue", None)
        if not isinstance(lower, (int, float)) or not isinstance(upper, (int, float)) or upper <= lower:
            continue
        rule = {"range": [float(lower), float(upper)], "paint": {key: base[key]}}
        label = _call(rng, "label", "") or ""
        if label:
            rule["label"] = label
        rules.append(rule)
    if not attribute or not rules:
        report.append("Graduated renderer without usable classes: the first symbol applies to everything.")
        base, outline = _symbol_paint(_first_symbol(renderer), family, report, "the symbol")
        return _style(family, base, outline, {"type": "simple"}), "single symbol"
    if _is_size_method(_call(renderer, "graduatedMethod", None)):
        report.append("Graduated by size: the classes keep their colours, the sizes do not vary on the web.")
    rules.sort(key=lambda r: r["range"][0])
    base, outline = _symbol_paint(_call(_call(renderer, "ranges")[0], "symbol"), family, report, "the first class")
    symbology = {"type": "graduated", "attribute": attribute, "interpolation": "step", "classCount": len(rules), "rules": rules}
    method = _call(_call(renderer, "classificationMethod", None), "id", None)
    if method in _SCHEMES:
        symbology["classificationScheme"] = _SCHEMES[method]
    return _style(family, base, outline, symbology), "graduated on {}, {} classes".format(attribute, len(rules))


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
        symbol = _call(rule, "symbol")
        if symbol is None:
            convertible = False
            break
        base, outline = _symbol_paint(symbol, family, report, "rule '{}'".format(_call(rule, "label", "")))
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
            fallback = _symbol_paint(_call(children[0], "symbol"), family, report, "the first rule")
        base, outline = fallback
        report.append("Rule-based renderer transmitted as categories on '{}'.".format(field))
        return _style(family, base, outline, {"type": "categorized", "attribute": field, "rules": rules}), \
            "categorized on {}, {} classes".format(field, len(rules))
    report.append("Rule-based renderer with expressions ALIGN cannot read: the first rule's symbol applies to everything.")
    first = _first_symbol(renderer)
    if first is None:
        return None, None
    base, outline = _symbol_paint(first, family, report, "the first rule")
    return _style(family, base, outline, {"type": "simple"}), "single symbol"


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
        report.append("{}: {} symbol layers, only the {} one is kept.".format(
            _cap(what), len(layers), "simple" if layer is not layers[0] else "bottom"))
    layer_type = _call(layer, "layerType", "") if layer is not None else ""
    if layer is not None and layer_type not in _FAITHFUL_LAYER_TYPES:
        report.append("{}: '{}' has no web equivalent, rendered as a flat {}.".format(
            _cap(what), layer_type or "unknown symbol layer", {"fill": "fill", "line": "line", "circle": "circle"}[family]))
    if layer is not None and _data_defined(layer):
        report.append("{}: properties driven by expressions are ignored.".format(_cap(what)))

    color = _color_of(layer, symbol, family, report, what)
    symbol_opacity = _call(symbol, "opacity", 1.0)
    opacity = round(float(symbol_opacity) * color[1], 3)
    base = {key: color[0], opacity_key: opacity}
    outline = None

    if family == "fill":
        if layer is not None and layer_type == "SimpleFill" and _call(layer, "brushStyle", 1) == 0:
            base[opacity_key] = 0.0
            report.append("{}: no brush, the polygon is transparent.".format(_cap(what)))
        outline = _outline_of(layer, report, what)
    elif family == "line":
        width = _px(_call(layer, "width", 0.26), _call(layer, "widthUnit", "mm"), report, "{} width".format(what))
        base["line-width"] = round(max(width, 0.5), 2)
        if layer is not None and _call(layer, "penStyle", 1) not in (1, None):
            report.append("{}: dash pattern not transmitted, solid line.".format(_cap(what)))
    else:
        size = _px(_call(layer, "size", 2.0), _call(layer, "sizeUnit", "mm"), report, "{} size".format(what))
        base["circle-radius"] = round(max(size / 2.0, 1.0), 2)
        if layer_type == "SimpleMarker" and not _is_circle(_call(layer, "shape", None)):
            report.append("{}: marker shape rendered as a circle.".format(_cap(what)))
        stroke = _hex(_call(layer, "strokeColor", None))
        if stroke:
            base["circle-stroke-color"] = stroke[0]
            base["circle-stroke-width"] = round(_px(_call(layer, "strokeWidth", 0.0), _call(layer, "strokeWidthUnit", "mm"), report, "{} stroke".format(what)), 2)
    return base, outline


def _outline_of(layer, report, what):
    if layer is None or _call(layer, "strokeStyle", 1) == 0:
        return None
    stroke = _hex(_call(layer, "strokeColor", None))
    width = _px(_call(layer, "strokeWidth", 0.26), _call(layer, "strokeWidthUnit", "mm"), report, "{} outline".format(what))
    if width <= 0:
        return None
    if width > _OUTLINE_MAX_WIDTH:
        report.append("{}: outline width reduced to {} px (ALIGN's maximum).".format(_cap(what), int(_OUTLINE_MAX_WIDTH)))
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
    report.append("{}: no readable colour, ALIGN's default blue applies.".format(_cap(what)))
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
            symbol = _call(items[0], "symbol")
            if symbol is not None:
                return symbol
    children = _call(_call(renderer, "rootRule"), "children", None)
    if children:
        symbol = _call(children[0], "symbol")
        if symbol is not None:
            return symbol
    try:
        from qgis.core import QgsRenderContext

        symbols = _call(renderer, "symbols", None, QgsRenderContext())
        if symbols:
            return symbols[0]
    except Exception:  # noqa: BLE001 — no render context available, the plain accessor is next
        pass
    return _call(renderer, "symbol", None)


def _is_size_method(method):
    """QGIS 3.x says ``1`` (GraduatedSize), QGIS 4 says ``Qgis.GraduatedMethod.Size``."""
    return method == 1 or getattr(method, "name", None) == "Size"


def _is_circle(shape):
    """``None`` (no shape accessor), ``8`` (QGIS 3.x Circle) or the enum member named Circle."""
    return shape is None or shape == 8 or getattr(shape, "name", None) == "Circle"


# ---------------------------------------------------------------------------
# Readers, pure and defensive
# ---------------------------------------------------------------------------

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
        report.append("{}: map-unit size has no screen equivalent, rendered at 1 mm.".format(_cap(what)))
        factor = _PX_PER_UNIT["mm"]
        amount = min(amount, 1.0) if amount > 0 else 1.0
    return round(amount * factor, 2)


def _unit_name(unit):
    if isinstance(unit, str):
        return unit.strip().lower()
    name = getattr(unit, "name", None)
    if isinstance(name, str):
        return name.lower()
    try:
        from qgis.core import QgsUnitTypes

        encoded = QgsUnitTypes.encodeUnit(unit)
        if isinstance(encoded, str):
            return encoded.lower()
    except Exception:  # noqa: BLE001 — any binding gap means "unknown unit", reported below
        pass
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
