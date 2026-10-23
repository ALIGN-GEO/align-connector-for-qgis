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
import base64
import pathlib
import sys
import tempfile
import types

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import qgis_stubs  # noqa: E402

qgis_stubs.install()

from align_connector.publish import symbology  # noqa: E402
from align_connector.publish.wording import describe_style  # noqa: E402


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


def label_settings(field="nom", expression=False, placement="AroundPoint", scales=None, **text):
    """A QgsPalLayerSettings stand-in: plain attributes, as the real class has, and its methods."""
    buffer = Stub(enabled=True, size=1.0, sizeUnit="MM", color=color("#ffffff"))
    text_format = Stub(**dict(dict(size=10.0, sizeUnit="Point", color=color("#1f2937"), buffer=buffer,
                                   font=Stub(family="Open Sans"), background=Stub(enabled=False), shadow=Stub(enabled=False)), **text))
    return types.SimpleNamespace(
        fieldName=field, isExpression=expression, drawLabels=True,
        placement=types.SimpleNamespace(name=placement) if placement else None,
        scaleVisibility=scales is not None, minimumScale=(scales or (0, 0))[0], maximumScale=(scales or (0, 0))[1],
        format=lambda: text_format, dataDefinedProperties=lambda: Stub(hasActiveProperties=False),
    )


def labelled(geometry, renderer, settings, kind="simple"):
    return Stub(geometryType=geometry, renderer=renderer, labelsEnabled=True, labeling=Stub(type=kind, settings=settings))


def marker_layer(path, fill="#d62828", **extra):
    attrs = dict(layerType="SvgMarker", path=path, color=color(fill), fillColor=color(fill), strokeColor=color("#111111"),
                 strokeWidth=0.5, strokeWidthUnit="MM", size=6.0, sizeUnit="MM", dataDefinedProperties=Stub(hasActiveProperties=False))
    attrs.update(extra)
    return Stub(**attrs)


QGIS_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<circle cx="12" cy="12" r="9" fill="param(fill) #000000" fill-opacity="param(fill-opacity) 1" '
    'stroke="param(outline) #232323" stroke-width="param(outline-width) 1"/></svg>'
)


def svg_file(markup=QGIS_SVG, name="pin.svg"):
    folder = pathlib.Path(tempfile.mkdtemp())
    path = folder / name
    path.write_text(markup, encoding="utf-8")
    return str(path)


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


def test_categorized_without_all_other_values_falls_back_to_neutral_grey_and_says_so():
    # TCK-2220: no "all other values" row. QGIS hides a feature outside every
    # class; the viewer draws it with the base paint, which must belong to no class.
    categories = [
        Stub(value="A", label="Zone A", symbol=symbol(fill_layer(fill="#f59e0b")), renderState=True),
        Stub(value="B", label="Zone B", symbol=symbol(fill_layer(fill="#10b981")), renderState=True),
    ]
    renderer = Stub(type="categorizedSymbol", classAttribute="zone", categories=categories)
    result = symbology.convert(vector(2, renderer))
    check(result.summary == "categorized on zone, 2 classes", result.summary)
    check(result.style["paint"] == {"fill-color": "#cbd5e1", "fill-opacity": 0.4}, result.style["paint"])
    check(result.style["outline"] == {"enabled": True, "width": 1.51, "color": "#1e3a8a"}, "the classes keep their outline")
    check([r["paint"]["fill-color"] for r in result.style["symbology"]["rules"]] == ["#f59e0b", "#10b981"], "classes untouched")
    check(any("neutral grey" in line for line in result.report), result.report)


def test_rule_based_without_else_falls_back_to_neutral_grey():
    rules = [
        Stub(filterExpression='"zone" = \'A\'', isElse=False, label="A", symbol=symbol(fill_layer(fill="#f59e0b")), children=[], active=True),
        Stub(filterExpression="zone = 12", isElse=False, label="", symbol=symbol(fill_layer(fill="#10b981")), children=[], active=True),
    ]
    renderer = Stub(type="RuleRenderer", rootRule=Stub(children=rules))
    result = symbology.convert(vector(2, renderer))
    check(result.summary == "categorized on zone, 2 classes", result.summary)
    check(result.style["paint"]["fill-color"] == "#cbd5e1", "no ELSE: neutral grey, not the first rule's colour")
    check(any("neutral grey" in line for line in result.report), result.report)


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


def test_simple_labels_travel_with_size_colour_halo_and_zoom_range():
    marker = Stub(layerType="SimpleMarker", color=color("#f59e0b"), size=2.0, sizeUnit="MM", dataDefinedProperties=Stub(hasActiveProperties=False))
    layer = labelled(0, Stub(type="singleSymbol", symbol=symbol(marker)), label_settings(scales=(25000, 1000)))
    result = symbology.convert(layer)
    check(result.style["labels"] == {
        "field": "nom", "size": 13.33, "color": "#1f2937", "haloColor": "#ffffff", "haloWidth": 3.78,
        "placement": "point", "minZoom": 13.5, "maxZoom": 18.2,
    }, result.style["labels"])
    check(result.summary == "single symbol, labels on nom", result.summary)
    check(any("Open Sans" in line for line in result.report), result.report)
    check(any("zoom levels" in line for line in result.report), result.report)
    check(not any("not transmitted" in line for line in result.report), result.report)


def test_labels_follow_a_line_and_report_what_alignment_cannot_follow():
    line = Stub(layerType="SimpleLine", color=color("#0f172a"), width=0.5, widthUnit="MM", penStyle=1, dataDefinedProperties=Stub(hasActiveProperties=False))
    result = symbology.convert(labelled(1, Stub(type="singleSymbol", symbol=symbol(line)), label_settings(placement="Curved")))
    check(result.style["labels"]["placement"] == "line", result.style["labels"])

    result = symbology.convert(labelled(2, Stub(type="singleSymbol", symbol=symbol(fill_layer())), label_settings(placement="PerimeterCurved")))
    check(result.style["labels"]["placement"] == "point", result.style["labels"])
    check(any("PerimeterCurved" in line for line in result.report), result.report)

    big = label_settings(size=200.0, buffer=Stub(enabled=True, size=5.0, sizeUnit="MM", color=color("#ffffff")))
    result = symbology.convert(labelled(2, Stub(type="singleSymbol", symbol=symbol(fill_layer())), big))
    check(result.style["labels"]["size"] == 72 and result.style["labels"]["haloWidth"] == 10, result.style["labels"])
    check(sum("within ALIGN's range" in line for line in result.report) == 2, result.report)


def test_an_expression_label_travels_only_when_it_is_a_field():
    renderer = Stub(type="singleSymbol", symbol=symbol(fill_layer()))
    result = symbology.convert(labelled(2, renderer, label_settings(field='"code ilot"', expression=True)))
    check(result.style["labels"]["field"] == "code ilot", result.style["labels"])

    result = symbology.convert(labelled(2, renderer, label_settings(field="upper(nom)", expression=True)))
    check("labels" not in result.style, result.style)
    check(any("upper(nom)" in line and "not transmitted" in line for line in result.report), result.report)

    result = symbology.convert(labelled(2, renderer, label_settings(), kind="rule-based"))
    check("labels" not in result.style and any("rule-based" in line for line in result.report), result.report)

    result = symbology.convert(labelled(2, Stub(type="nullSymbol"), label_settings()))
    check(result.style is None and any("travel with a style" in line for line in result.report), result.report)


def test_an_svg_marker_travels_with_its_outline_written_and_its_fill_left_to_align():
    renderer = Stub(type="singleSymbol", symbol=symbol(marker_layer(svg_file())))
    result = symbology.convert(vector(0, renderer))
    images = result.style["images"]
    check(images["markerColor"] == "#d62828" and images["markerSize"] == 22.68, images)
    svg = images["markerSvg"]
    # The fill stays a parameter: ALIGN colours the marker, per class when there are classes.
    check('fill="param(fill) #000000"' in svg, svg)
    # 0.5 mm of outline on a 6 mm marker whose own width is 24: 2 units, as QGIS draws it.
    check('stroke="#111111"' in svg and 'stroke-width="2"' in svg and 'fill-opacity="1"' in svg, svg)
    check(result.summary == "single symbol, SVG marker", result.summary)
    check(not any("flat" in line for line in result.report), result.report)


def test_categorized_markers_share_one_svg_and_differing_ones_are_reported():
    path = svg_file()
    categories = [
        Stub(value="A", label="A", symbol=symbol(marker_layer(path, fill="#f59e0b")), renderState=True),
        Stub(value="B", label="B", symbol=symbol(marker_layer(path, fill="#10b981")), renderState=True),
    ]
    result = symbology.convert(vector(0, Stub(type="categorizedSymbol", classAttribute="zone", categories=categories)))
    check("markerSvg" in result.style["images"], result.style)
    check([r["paint"]["circle-color"] for r in result.style["symbology"]["rules"]] == ["#f59e0b", "#10b981"], result.style["symbology"])

    categories[1] = Stub(value="B", label="B", symbol=symbol(marker_layer(svg_file(name="other.svg"))), renderState=True)
    result = symbology.convert(vector(0, Stub(type="categorizedSymbol", classAttribute="zone", categories=categories)))
    check("images" not in result.style, result.style)
    check(any("different images" in line and "plain circle" in line for line in result.report), result.report)


def test_an_svg_fill_travels_as_a_pattern_written_and_sized_as_qgis_draws_it():
    layer = fill_layer(layer_type="SVGFill", svgFilePath=svg_file(), svgFillColor=color("#b45309"), svgStrokeColor=color("#78350f", 0.5),
                       svgStrokeWidth=0.2, svgStrokeWidthUnit="MM", patternWidth=5.0, patternWidthUnit="MM")
    result = symbology.convert(vector(2, Stub(type="singleSymbol", symbol=symbol(layer))))
    pattern = result.style["images"]["fillPatternSvg"]
    check(pattern.startswith('<svg width="18.9" height="18.9" xmlns='), pattern[:80])
    check('fill="#b45309"' in pattern and 'stroke="#78350f"' in pattern and 'stroke-opacity' not in pattern, pattern)
    check("param(" not in pattern, pattern)
    check(result.summary == "single symbol, SVG pattern", result.summary)
    check(not any("flat" in line for line in result.report), result.report)


def test_an_svg_that_cannot_travel_is_said_before_the_send():
    cases = [
        (str(pathlib.Path(tempfile.mkdtemp()) / "gone.svg"), "not found"),
        ("https://icons.example/pin.svg", "on the web"),
        (svg_file('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'), "self-contained"),
        (svg_file('<svg xmlns="http://www.w3.org/2000/svg"><image href="https://tracker.example/p.png"/></svg>'), "self-contained"),
        (svg_file('<svg xmlns="http://www.w3.org/2000/svg"><path d="{}"/></svg>'.format("M0 0" * 70000)), "256 KB"),
        (svg_file("not an image"), "not SVG"),
    ]
    for path, reason in cases:
        result = symbology.convert(vector(0, Stub(type="singleSymbol", symbol=symbol(marker_layer(path)))))
        check("images" not in result.style, (path, result.style))
        check(any(reason in line and "plain circle" in line for line in result.report), (reason, result.report))

    embedded = "base64:" + base64.b64encode(QGIS_SVG.encode("utf-8")).decode("ascii")
    result = symbology.convert(vector(0, Stub(type="singleSymbol", symbol=symbol(marker_layer(embedded)))))
    check(result.style["images"]["markerSvg"].startswith("<svg"), "an SVG embedded in the project travels")

    internal = svg_file('<svg xmlns="http://www.w3.org/2000/svg"><defs><path id="a" d="M0 0h1v1z"/></defs><use href="#a"/></svg>')
    result = symbology.convert(vector(0, Stub(type="singleSymbol", symbol=symbol(marker_layer(internal)))))
    check("images" in result.style, "a reference inside the file is self-contained (#527)")


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
        test_simple_labels_travel_with_size_colour_halo_and_zoom_range,
        test_labels_follow_a_line_and_report_what_alignment_cannot_follow,
        test_an_expression_label_travels_only_when_it_is_a_field,
        test_an_svg_marker_travels_with_its_outline_written_and_its_fill_left_to_align,
        test_categorized_markers_share_one_svg_and_differing_ones_are_reported,
        test_an_svg_fill_travels_as_a_pattern_written_and_sized_as_qgis_draws_it,
        test_an_svg_that_cannot_travel_is_said_before_the_send,
    ]
    for test in tests:
        test()
        print("ok  {}".format(test.__name__))
    print("{} checks passed".format(len(tests)))


if __name__ == "__main__":
    main()
