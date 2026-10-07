#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove what the plugin makes of a QGIS symbology, and what it says it lost.

    python scripts/smoke_symbology.py

Runs on a plain interpreter — no QGIS. Renderers, symbols and symbol layers
are stand-ins that answer the same method names the real ones do; what is
under test is the conversion to ALIGN's canonical style (TCK-2201) and the
report a publisher reads BEFORE sending:

* a simple fill, line or marker keeps colour, opacity, outline and size, with
  millimetres turned into screen pixels;
* a categorized or graduated renderer keeps its classes, labels and fallback;
* a rule-based renderer made of equalities on one field becomes categories,
  any other one degrades to a single symbol and says so;
* hatches, SVG markers, dash patterns, map units, expressions and labels are
  each named in the report, never silently dropped.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import pathlib
import sys
import types

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import qgis_stubs  # noqa: E402

qgis_stubs.install()

from align_connector.publish import symbology  # noqa: E402
from align_connector.publish.dialog import describe_style  # noqa: E402


class Stub(object):
    """An object whose methods return the values given at construction."""

    def __init__(self, **attrs):
        self._attrs = attrs

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._attrs:
            value = self._attrs[name]
            return lambda *args: value
        raise AttributeError(name)


def color(hex_value, alpha=1.0):
    return Stub(name=hex_value, alphaF=alpha)


def fill_layer(fill="#1d4ed8", alpha=1.0, stroke="#1e3a8a", stroke_width=0.4, unit="MM", layer_type="SimpleFill", **extra):
    attrs = dict(
        layerType=layer_type,
        fillColor=color(fill, alpha),
        color=color(fill, alpha),
        strokeColor=color(stroke),
        strokeWidth=stroke_width,
        strokeWidthUnit=unit,
        strokeStyle=1,
        brushStyle=1,
        dataDefinedProperties=Stub(hasActiveProperties=False),
    )
    attrs.update(extra)
    return Stub(**attrs)


def symbol(*layers, **extra):
    attrs = dict(symbolLayers=list(layers), color=layers[0].color() if layers else color("#000000"), opacity=1.0)
    attrs.update(extra)
    return Stub(**attrs)


def vector(geometry, renderer, labels=False):
    return Stub(geometryType=geometry, renderer=renderer, labelsEnabled=labels, labeling=Stub() if labels else None)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def test_a_simple_fill_keeps_colour_opacity_and_outline_in_pixels():
    renderer = Stub(type="singleSymbol", symbol=symbol(fill_layer(alpha=0.5), opacity=0.8))
    result = symbology.convert(vector(2, renderer))
    check(result.summary == "single symbol", result.summary)
    check(result.report == [], result.report)
    check(result.style["renderType"] == "fill", result.style)
    check(result.style["paint"] == {"fill-color": "#1d4ed8", "fill-opacity": 0.4}, result.style["paint"])
    check(result.style["symbology"] == {"type": "simple"}, result.style["symbology"])
    check(result.style["outline"] == {"enabled": True, "width": 1.51, "color": "#1e3a8a"}, result.style["outline"])


def test_a_hatched_fill_and_labels_are_reported_not_dropped():
    layer = fill_layer(layer_type="LinePatternFill")
    renderer = Stub(type="singleSymbol", symbol=symbol(layer))
    result = symbology.convert(vector(2, renderer, labels=True))
    check(result.style["paint"]["fill-color"] == "#1d4ed8", "the pattern's colour still paints the polygon")
    check(any("Labels" in line for line in result.report), result.report)
    check(any("LinePatternFill" in line and "flat" in line for line in result.report), result.report)


def test_categorized_keeps_classes_labels_fallback_and_reports_hidden_ones():
    categories = [
        Stub(value="A", label="Zone A", symbol=symbol(fill_layer(fill="#f59e0b")), renderState=True),
        Stub(value="B", label="", symbol=symbol(fill_layer(fill="#10b981")), renderState=True),
        Stub(value="C", label="Hidden", symbol=symbol(fill_layer(fill="#000000")), renderState=False),
        Stub(value="", label="all other values", symbol=symbol(fill_layer(fill="#9ca3af")), renderState=True),
    ]
    renderer = Stub(type="categorizedSymbol", classAttribute="zone", categories=categories)
    result = symbology.convert(vector(2, renderer))
    check(result.summary == "categorized on zone, 2 classes", result.summary)
    check(result.style["symbology"]["attribute"] == "zone", result.style["symbology"])
    check(result.style["symbology"]["rules"] == [
        {"value": "A", "paint": {"fill-color": "#f59e0b"}, "label": "Zone A"},
        {"value": "B", "paint": {"fill-color": "#10b981"}},
    ], result.style["symbology"]["rules"])
    check(result.style["paint"]["fill-color"] == "#9ca3af", "the 'all other values' colour is the fallback")
    check(any("Hidden" in line for line in result.report), result.report)


def test_graduated_keeps_sorted_classes_and_the_scheme():
    ranges = [
        Stub(lowerValue=50.0, upperValue=90.0, label="50 - 90", symbol=symbol(fill_layer(fill="#fde68a")), renderState=True),
        Stub(lowerValue=0.0, upperValue=50.0, label="0 - 50", symbol=symbol(fill_layer(fill="#fef3c7")), renderState=True),
        Stub(lowerValue=90.0, upperValue=130.0, label="90 - 130", symbol=symbol(fill_layer(fill="#f59e0b")), renderState=True),
    ]
    renderer = Stub(
        type="graduatedSymbol", classAttribute="speed", ranges=ranges,
        classificationMethod=Stub(id="Quantile"), graduatedMethod=0,
    )
    result = symbology.convert(vector(1, renderer))
    sym = result.style["symbology"]
    check(result.summary == "graduated on speed, 3 classes", result.summary)
    check(sym["type"] == "graduated" and sym["interpolation"] == "step", sym)
    check(sym["classificationScheme"] == "quantile" and sym["classCount"] == 3, sym)
    check([r["range"] for r in sym["rules"]] == [[0.0, 50.0], [50.0, 90.0], [90.0, 130.0]], sym["rules"])
    check(sym["rules"][0]["paint"] == {"line-color": "#fef3c7"}, sym["rules"][0])
    check(result.style["renderType"] == "line", result.style)


def test_rule_based_equalities_on_one_field_become_categories():
    rules = [
        Stub(filterExpression='"zone" = \'A\'', isElse=False, label="A", symbol=symbol(fill_layer(fill="#f59e0b")), children=[], active=True),
        Stub(filterExpression="zone = 12", isElse=False, label="", symbol=symbol(fill_layer(fill="#10b981")), children=[], active=True),
        Stub(filterExpression="ELSE", isElse=True, label="other", symbol=symbol(fill_layer(fill="#9ca3af")), children=[], active=True),
    ]
    renderer = Stub(type="RuleRenderer", rootRule=Stub(children=rules))
    result = symbology.convert(vector(2, renderer))
    check(result.summary == "categorized on zone, 2 classes", result.summary)
    check([r["value"] for r in result.style["symbology"]["rules"]] == ["A", 12], result.style["symbology"]["rules"])
    check(result.style["paint"]["fill-color"] == "#9ca3af", "ELSE is the fallback")
    check(any("transmitted as categories" in line for line in result.report), result.report)


def test_rule_based_with_other_expressions_degrades_and_says_so():
    rules = [
        Stub(filterExpression='"pop" > 1000', isElse=False, label="big", symbol=symbol(fill_layer(fill="#f59e0b")), children=[], active=True),
        Stub(filterExpression='"pop" <= 1000', isElse=False, label="small", symbol=symbol(fill_layer(fill="#10b981")), children=[], active=True),
    ]
    renderer = Stub(type="RuleRenderer", rootRule=Stub(children=rules))
    result = symbology.convert(vector(2, renderer))
    check(result.summary == "single symbol", result.summary)
    check(result.style["paint"]["fill-color"] == "#f59e0b", "the first rule's symbol applies")
    check(any("cannot read" in line for line in result.report), result.report)


def test_a_marker_keeps_size_and_stroke_and_reports_its_shape():
    layer = Stub(
        layerType="SimpleMarker", color=color("#f59e0b"), strokeColor=color("#111111"), strokeWidth=0.2,
        strokeWidthUnit="MM", size=3.0, sizeUnit="MM", shape=Stub(name="Star"),
        dataDefinedProperties=Stub(hasActiveProperties=True),
    )
    renderer = Stub(type="singleSymbol", symbol=symbol(layer))
    result = symbology.convert(vector(0, renderer))
    paint = result.style["paint"]
    check(result.style["renderType"] == "circle", result.style)
    check(paint["circle-color"] == "#f59e0b" and paint["circle-radius"] == 5.67, paint)
    check(paint["circle-stroke-color"] == "#111111" and paint["circle-stroke-width"] == 0.76, paint)
    check(any("shape" in line for line in result.report), result.report)
    check(any("expressions are ignored" in line for line in result.report), result.report)


def test_a_dashed_line_keeps_its_width_and_reports_the_dash():
    layer = Stub(layerType="SimpleLine", color=color("#0f172a"), width=0.5, widthUnit="Point", penStyle=2,
                 dataDefinedProperties=Stub(hasActiveProperties=False))
    renderer = Stub(type="singleSymbol", symbol=symbol(layer))
    result = symbology.convert(vector(1, renderer))
    check(result.style["paint"] == {"line-color": "#0f172a", "line-opacity": 1.0, "line-width": 0.67}, result.style["paint"])
    check(any("dash" in line.lower() for line in result.report), result.report)


def test_map_units_and_too_wide_outlines_are_reported():
    renderer = Stub(type="singleSymbol", symbol=symbol(fill_layer(unit="MapUnit", stroke_width=250)))
    result = symbology.convert(vector(2, renderer))
    check(result.style["outline"]["width"] == 3.78, result.style["outline"])
    check(any("map-unit" in line for line in result.report), result.report)

    wide = Stub(type="singleSymbol", symbol=symbol(fill_layer(stroke_width=10)))
    result = symbology.convert(vector(2, wide))
    check(result.style["outline"]["width"] == 20, result.style["outline"])
    check(any("maximum" in line for line in result.report), result.report)


def test_heatmap_cluster_null_and_unknown_renderers():
    heat = Stub(type="heatmapRenderer", radius=10.0, radiusUnit="MM")
    result = symbology.convert(vector(0, heat))
    check(result.style == {"renderType": "heatmap", "paint": {"heatmap-radius": 37.8}}, result.style)

    inner = Stub(type="singleSymbol", symbol=symbol(Stub(layerType="SimpleMarker", color=color("#f59e0b"), size=2.0, sizeUnit="MM",
                                                           dataDefinedProperties=Stub(hasActiveProperties=False))))
    cluster = Stub(type="pointCluster", embeddedRenderer=inner, tolerance=3.0, toleranceUnit="MM")
    result = symbology.convert(vector(0, cluster))
    check(result.style["cluster"] == {"enabled": True, "radius": 11.34}, result.style)
    check(result.summary == "single symbol (clustered)", result.summary)

    result = symbology.convert(vector(2, Stub(type="nullSymbol")))
    check(result.style is None and any("invisible" in line for line in result.report), result.report)

    result = symbology.convert(vector(2, Stub(type="25dRenderer")))
    check(result.style is None and any("25dRenderer" in line for line in result.report), result.report)

    # QGIS 4 answers an enum member (a plain `name` attribute), not an int.
    enum_member = types.SimpleNamespace(name="Polygon", value=2)
    result = symbology.convert(vector(enum_member, Stub(type="singleSymbol", symbol=symbol(fill_layer()))))
    check(result.style["renderType"] == "fill", "a QGIS 4 enum geometry type is read by name")


def test_the_dialog_sentence_says_what_travels_and_what_was_simplified():
    check(describe_style(symbology.Conversion(None, None, [])) == "Style: ALIGN's default will apply.", "no style")
    check(describe_style(symbology.Conversion({"renderType": "fill"}, "categorized on zone, 4 classes", []))
          == "Style: categorized on zone, 4 classes.", "clean")
    sentence = describe_style(symbology.Conversion({"renderType": "fill"}, "single symbol", ["Labels are not transmitted yet.", "The symbol: 'SVGFill' has no web equivalent, rendered as a flat fill."]))
    check(sentence.startswith("Style: single symbol. Simplified: Labels are not transmitted yet. The symbol"), sentence)


def main():
    tests = [
        test_a_simple_fill_keeps_colour_opacity_and_outline_in_pixels,
        test_a_hatched_fill_and_labels_are_reported_not_dropped,
        test_categorized_keeps_classes_labels_fallback_and_reports_hidden_ones,
        test_graduated_keeps_sorted_classes_and_the_scheme,
        test_rule_based_equalities_on_one_field_become_categories,
        test_rule_based_with_other_expressions_degrades_and_says_so,
        test_a_marker_keeps_size_and_stroke_and_reports_its_shape,
        test_a_dashed_line_keeps_its_width_and_reports_the_dash,
        test_map_units_and_too_wide_outlines_are_reported,
        test_heatmap_cluster_null_and_unknown_renderers,
        test_the_dialog_sentence_says_what_travels_and_what_was_simplified,
    ]
    for test in tests:
        test()
        print("ok  {}".format(test.__name__))
    print("{} checks passed".format(len(tests)))


if __name__ == "__main__":
    main()
