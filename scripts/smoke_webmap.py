#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove how a finished lot becomes the web map that shows it (TCK-2204).

    python scripts/smoke_webmap.py

Runs on a plain interpreter — no QGIS, no network. What is under test:

* the follower that pairs each task with its request and fires once, on the
  last outcome, whatever the order;
* the request built from the lot: successful items only, the display-ready
  copy for a raster, the panel's order, visibility, geometry and the style
  that was sent; what did not land is named;
* the camera: centre and Web Mercator zoom at which a WGS 84 extent fits a
  canvas of a given size;
* the reading of ALIGN's answer, and the sentence the user reads at the end.

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

from align_connector import api, config, net  # noqa: E402
from align_connector.panel import webmap  # noqa: E402
from align_connector.panel.follow import LotFollower  # noqa: E402


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def _request(title, visible=True, geometry="Polygon", style=None):
    return types.SimpleNamespace(
        plan=types.SimpleNamespace(title=title),
        visible=visible,
        geometry_type=geometry,
        briefing=types.SimpleNamespace(style=style),
    )


def _task(asset_id, servable=None, derivation_error=None):
    return types.SimpleNamespace(asset_id=asset_id, servable_asset_id=servable, derivation_error=derivation_error)


# -- the follower -------------------------------------------------------------

def test_the_follower_fires_once_on_the_last_outcome_whatever_the_order():
    fired = []
    follower = LotFollower(types.SimpleNamespace(web_map_name="Site"), lambda f: fired.append(f))
    a, b, c = _task("a"), _task("b"), _task("c")
    follower.started(a, _request("A"))
    follower.started(b, _request("B"))
    follower.started(c, _request("C"))
    follower.finished(c, True)
    follower.finished(a, False)
    check(fired == [] and not follower.done, "two of three: not yet")
    follower.finished(b, True)
    check(fired == [follower] and follower.done, "the last outcome fires, once")
    follower.finished(b, True)
    check(len(fired) == 1, "a repeated outcome does not fire again")
    check([(r.plan.title, ok) for r, _t, ok in follower.records()] == [("A", False), ("B", True), ("C", True)],
          "records keep the start order, not the finish order")
    check(follower.finished(_task("x"), True) is None and len(fired) == 1, "a stranger is ignored")


# -- the request ---------------------------------------------------------------

def test_the_request_keeps_the_successful_items_in_order_with_what_the_map_needs():
    style = {"renderType": "fill", "paint": {"fill-color": "#ff0000"}}
    records = [
        (_request("Parcels", style=style), _task("parcels"), True),
        (_request("Elevation", visible=False, geometry=None), _task("tiff", servable="cog-1"), True),
        (_request("Broken"), _task("broken"), False),
        (_request("Stored only"), _task("stored", derivation_error="GDAL failed"), True),
        (_request("Roads", geometry="LineString"), _task("roads"), True),
    ]
    plan = webmap.plan(records)
    check([e["assetId"] for e in plan.layers] == ["parcels", "cog-1", "roads"], plan.layers)
    check(plan.layers[0] == {"assetId": "parcels", "title": "Parcels", "visible": True, "geometryType": "Polygon", "style": style}, plan.layers[0])
    check(plan.layers[1] == {"assetId": "cog-1", "title": "Elevation", "visible": False}, "a raster references its display-ready copy, no geometry, no style")
    check(plan.left_out == ["Broken", "Stored only"], plan.left_out)


# -- the camera --------------------------------------------------------------------

def test_the_camera_centres_the_extent_and_fits_it_on_the_canvas():
    view = webmap.map_view_of((4.30, 50.80, 4.40, 50.90), 1000, 700)
    check(view["center"] == [4.35, 50.85], view)
    # One tenth of a degree across 1000 px: zoom ~11.8 by longitude; the
    # latitude span fits at a slightly lower zoom on a 700 px canvas.
    check(11.0 <= view["zoom"] <= 12.0, view)
    tall = webmap.map_view_of((4.30, 50.80, 4.40, 51.90), 1000, 700)
    check(tall["zoom"] < view["zoom"], "a taller extent needs a lower zoom")
    world = webmap.map_view_of((-180.0, -85.0, 180.0, 85.0), 512, 512)
    check(world["zoom"] == 0.0, world)
    check(webmap.map_view_of((4.3, 50.8, 4.3, 50.9), 1000, 700) is None, "a degenerate extent has no camera")
    check(webmap.map_view_of((4.3, 50.8, 4.4, 50.9), 0, 700) is None, "no canvas, no camera")


# -- the answer ----------------------------------------------------------------------

class _Session(object):
    def __init__(self, token="alg_at_test"):
        self._token = token

    def access_token(self):
        return self._token


def _answer(status, body):
    calls = []

    def post_json(url, payload, bearer=None):
        calls.append((url, payload, bearer))
        return net.HttpResult(status, body)

    api.net.post_json = post_json
    return calls


def test_the_request_travels_and_the_answer_is_read():
    calls = _answer(200, {"assetId": "wm-1", "name": "Site", "layers": [{"assetId": "parcels", "layerId": "parcels"}], "skipped": [{"assetId": "x", "reason": "not available to you"}], "extra": 1})
    answer = api.create_web_map(_Session(), "org-1", "Site", [{"assetId": "parcels"}], map_view={"center": [4.35, 50.85], "zoom": 12}, description="From QGIS", visibility="org")
    url, payload, bearer = calls[0]
    check(url == config.web_map_endpoint("org-1") and url.endswith("/api/v1/orgs/org-1/datastore/web-maps/from-assets"), url)
    check(payload == {"name": "Site", "layers": [{"assetId": "parcels"}], "mapView": {"center": [4.35, 50.85], "zoom": 12}, "description": "From QGIS", "visibility": "org"}, payload)
    check(bearer == "alg_at_test", "the publish token must travel as bearer")
    check(answer == {"assetId": "wm-1", "name": "Site", "layers": [{"assetId": "parcels", "layerId": "parcels"}], "skipped": [{"assetId": "x", "reason": "not available to you"}]}, answer)

    _answer(409, {"error": "PROJECT_CAP_REACHED", "message": "Project limit reached: this organization already holds 3 projects."})
    try:
        api.create_web_map(_Session(), "org-1", "Site", [{"assetId": "parcels"}])
    except api.ApiError as exc:
        check(exc.status == 409 and "Project limit reached" in exc.message, exc.message)
    else:
        raise AssertionError("a refusal must be raised, in the server's words")

    _answer(200, {"name": "no id"})
    try:
        api.create_web_map(_Session(), "org-1", "Site", [{"assetId": "parcels"}])
    except api.ApiError as exc:
        check("unexpected answer" in exc.message, exc.message)
    else:
        raise AssertionError("an answer without an id is not a web map")

    check(config.web_map_url("org-1", "wm-1").endswith("/app/org-1/map-viewer?webmap=wm-1"), config.web_map_url("org-1", "wm-1"))


def test_the_sentence_says_what_is_on_the_map_and_what_is_not():
    done = types.SimpleNamespace(name="Site", org_name="Town GIS", placed=[{"assetId": "a"}, {"assetId": "b"}], skipped=[{"assetId": "x", "title": "Ortho", "reason": "not available to you"}], error_message=None)
    text = webmap.describe_outcome(done, True, ["Broken"])
    check(text == "Web map “Site” is ready in Town GIS with 2 layers. Not placed: Ortho (not available to you), Broken (its publication did not succeed).", text)
    one = types.SimpleNamespace(name="Site", org_name="Town GIS", placed=[{"assetId": "a"}], skipped=[], error_message=None)
    check(webmap.describe_outcome(one, True, []) == "Web map “Site” is ready in Town GIS with 1 layer.", "singular")
    failed = types.SimpleNamespace(name="Site", org_name="Town GIS", placed=[], skipped=[], error_message="Project limit reached.")
    check(webmap.describe_outcome(failed, False, []) == "Project limit reached.", "a refusal speaks in the server's words")
    mute = types.SimpleNamespace(name="Site", org_name="Town GIS", placed=[], skipped=[], error_message=None)
    check(webmap.describe_outcome(mute, False, []) == "The web map could not be created.", "a failure without words still says so")


def main():
    tests = [
        test_the_follower_fires_once_on_the_last_outcome_whatever_the_order,
        test_the_request_keeps_the_successful_items_in_order_with_what_the_map_needs,
        test_the_camera_centres_the_extent_and_fits_it_on_the_canvas,
        test_the_request_travels_and_the_answer_is_read,
        test_the_sentence_says_what_is_on_the_map_and_what_is_not,
    ]
    for test in tests:
        test()
        print("ok  {}".format(test.__name__))
    print("{} checks passed".format(len(tests)))


if __name__ == "__main__":
    main()
