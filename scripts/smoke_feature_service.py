#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove the decisions of publishing a layer as an editable feature service.

    python scripts/smoke_feature_service.py

Runs on a plain interpreter — no QGIS, no network. The transport is replaced
by canned answers; what is under test (TCK-2320, ADR-0063 §3):

* which layer of the uploaded file ALIGN is asked to publish, or why not;
* following ALIGN's job to its end, a failure in ALIGN's words, a cancelled
  follow that leaves the job running, a job that never ends;
* what the user is told, and which item the "Open in ALIGN" link points to;
* the OAPIF data source and the OAuth2 configuration that signs it (PKCE, a
  narrower grant than the plugin's, no secret);
* the three API calls and their refusals (a token from before the
  ``features`` scopes asks to sign in again);
* the batch: vector layers become new services, never a replacement, and the
  button says so; the coordinate system a service keeps.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import qgis_stubs  # noqa: E402

qgis_stubs.install()

from align_connector import api, config, net  # noqa: E402
from align_connector.panel import batch  # noqa: E402
from align_connector.panel.outcome import INFO, SUCCESS, outcome_of  # noqa: E402
from align_connector.publish import exporter, feature_service, memory  # noqa: E402
from align_connector.publish.feature_service import ServicePublishFailed  # noqa: E402


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def raises(call, error):
    try:
        call()
    except error as exc:
        return exc
    raise AssertionError("expected {}".format(error.__name__))


class _Session(object):
    def __init__(self, token="alg_at_test"):
        self._token = token

    def access_token(self):
        return self._token


def _answer(status, body):
    """Replace both verbs of the transport with one canned answer, and record the calls."""
    calls = []

    def post_json(url, payload, bearer=None):
        calls.append(("POST", url, payload, bearer))
        return net.HttpResult(status, body)

    def get_json(url, bearer=None):
        calls.append(("GET", url, None, bearer))
        return net.HttpResult(status, body)

    api.net.post_json = post_json
    api.net.get_json = get_json
    return calls


# -- the layer ---------------------------------------------------------------


def test_picks_the_one_layer_or_says_why_not():
    layer = {"name": "parcels", "featureCount": 12, "geometryType": "MultiPolygon"}
    check(feature_service.pick_layer({"layers": [layer]}) is layer, "the uploaded file holds one layer")
    refused = raises(
        lambda: feature_service.pick_layer({"layers": [{"name": "mix", "refusal": "The layer mixes points and lines."}]}),
        ServicePublishFailed,
    )
    check(str(refused) == "The layer mixes points and lines.", "ALIGN's refusal is the user's message")
    for empty in ({}, {"layers": []}, {"layers": ["x"]}, None):
        raises(lambda e=empty: feature_service.pick_layer(e), ServicePublishFailed)


# -- the job -----------------------------------------------------------------


def _jobs(*states):
    answers = list(states)
    seen = []

    def fetch():
        seen.append(1)
        return answers.pop(0)

    return fetch, seen


def test_follows_the_job_to_its_end_with_its_progress():
    fetch, _ = _jobs(
        {"status": "pending"},
        {"status": "running", "progress": {"loaded": 500, "total": 1000}},
        {"status": "ready", "progress": {"loaded": 1000, "total": 1000}, "result": {"loaded": 1000}},
    )
    waits, progress = [], []
    job = feature_service.follow(fetch, lambda: waits.append(1), lambda: False, lambda a, b: progress.append((a, b)))
    check(job["status"] == "ready", job)
    check(progress == [(500, 1000), (1000, 1000)], progress)
    check(len(waits) == 2, "one pause between two polls, none after the end")


def test_a_failed_job_speaks_in_alignss_words():
    fetch, _ = _jobs({"status": "failed", "error": "The file holds no feature."})
    failed = raises(lambda: feature_service.follow(fetch, lambda: None, lambda: False), ServicePublishFailed)
    check(str(failed) == "The file holds no feature.", str(failed))
    fetch, _ = _jobs({"status": "failed"})
    failed = raises(lambda: feature_service.follow(fetch, lambda: None, lambda: False), ServicePublishFailed)
    check("could not publish" in str(failed), "a failure without a reason still says something")


def test_a_cancelled_follow_stops_polling_and_a_job_that_never_ends_is_told():
    fetch, seen = _jobs()
    check(feature_service.follow(fetch, lambda: None, lambda: True) is None, "cancelled: no job, no poll")
    check(seen == [], "nothing fetched once cancelled")

    def running():
        return {"status": "running"}

    late = raises(lambda: feature_service.follow(running, lambda: None, lambda: False, max_polls=3), ServicePublishFailed)
    check("will appear in your Datastore" in str(late), str(late))


# -- what the user is told -----------------------------------------------------


def test_describes_the_result():
    text = feature_service.describe_result(
        {"loaded": 1200, "storageSrid": 2154, "repaired": 3, "withoutGeometry": 1, "warnings": ["Field \"é\" renamed e."]}
    )
    check(text.startswith("1200 features published as an editable feature service."), text)
    check("EPSG:2154" in text and "3 invalid geometries" in text and "1 features have no geometry" in text, text)
    check(text.endswith("Field \"é\" renamed e."), text)
    check(feature_service.describe_result({"loaded": 5}) == "5 features published as an editable feature service.", "only what happened")


class _Task(object):
    def __init__(self, **values):
        self.asset_name = "Parcels"
        self.org_name = "Ville"
        self.as_service = True
        self.asset_id = "file-1"
        self.service_id = "svc-1"
        self.service_result = None
        for key, value in values.items():
            setattr(self, key, value)


def test_the_outcome_links_the_service_not_the_file():
    done = outcome_of(_Task(service_result={"loaded": 3}), True)
    check(done.level == SUCCESS and done.asset_id == "svc-1", (done.level, done.asset_id))
    check(done.text.startswith("“Parcels” is now an editable feature service in Ville on ALIGN. 3 features"), done.text)

    pending = outcome_of(_Task(service_pending=True), True)
    check(pending.level == INFO and pending.asset_id == "svc-1", (pending.level, pending.asset_id))
    check("still preparing" in pending.text, pending.text)


# -- what QGIS opens --------------------------------------------------------------


def test_the_oapif_source_quotes_every_value():
    source = feature_service.oapif_source("https://api.example/ogc", "lay'er", "abc1234", srid=2154)
    check("typename='lay\\'er'" in source, source)
    check("url='https://api.example/ogc'" in source and "authcfg='abc1234'" in source, source)
    check(source.endswith("srsname='EPSG:2154'"), source)
    check("srsname" not in feature_service.oapif_source("https://x", "l", "a"), "no projection asked when unknown")


def test_the_oauth2_configuration_is_pkce_features_only_and_secret_free():
    settings = json.loads(
        feature_service.oauth2_settings(
            "ALIGN feature services",
            config.authorize_url(),
            config.token_endpoint(),
            config.CLIENT_ID,
            config.SERVICE_LAYER_SCOPES,
        )
    )
    check(settings["grantFlow"] == 3, "PKCE: the only public-client flow ALIGN accepts")
    check(settings["configType"] == 1 and settings["accessMethod"] == 0, "a custom configuration, sent as a header")
    check(settings["scope"] == "features:read features:write", "features only: not the Datastore")
    check(settings["clientSecret"] == "" and settings["password"] == "", "a public client holds no secret")
    check(settings["clientId"] == "align-connector-qgis", settings["clientId"])
    check(settings["requestUrl"].endswith("/oauth/authorize") and settings["tokenUrl"] == settings["refreshTokenUrl"], settings)
    check(settings["persistToken"] is True, "a restart does not ask again")
    check(feature_service.can_edit_in_qgis(33400) and feature_service.can_edit_in_qgis(34400), "3.34 and later")
    check(not feature_service.can_edit_in_qgis(33200) and not feature_service.can_edit_in_qgis(None), "before 3.34: no PKCE")


# -- the API ---------------------------------------------------------------------


def test_starts_the_service_and_removes_the_file_by_default():
    calls = _answer(202, {"jobId": "job-1", "serviceId": "svc-1", "status": "pending"})
    job = api.start_feature_service(_Session(), "org-1", "file-1", "parcels", "Parcels", visibility="org")
    check(job["jobId"] == "job-1", job)
    verb, url, payload, bearer = calls[0]
    check(verb == "POST" and url.endswith("/api/v1/orgs/org-1/feature-services/publications"), url)
    check(payload == {"assetId": "file-1", "layerName": "parcels", "title": "Parcels", "keepSource": False, "visibility": "org"}, payload)
    check(bearer == "alg_at_test", "the token travels as bearer")

    calls = _answer(200, {"jobId": "job-1", "status": "running"})
    api.feature_service_job(_Session(), "org-1", "job-1")
    check(calls[0][0] == "GET" and calls[0][1].endswith("/feature-services/publications/job-1"), calls)

    calls = _answer(200, {"layers": []})
    api.preview_feature_service(_Session(), "org-1", "file-1")
    check(calls[0][1].endswith("/feature-services/publications/preview") and calls[0][2] == {"assetId": "file-1"}, calls)


def test_a_token_without_the_features_scopes_asks_to_sign_in_again():
    _answer(403, {"error": "INSUFFICIENT_SCOPE", "message": "Missing scope features:write"})
    refused = raises(lambda: api.start_feature_service(_Session(), "org-1", "f", "l", "t"), api.ApiError)
    check(refused.needs_sign_in and "Sign in to ALIGN again" in refused.message, refused.message)


def test_the_refusals_that_say_what_to_do_keep_alignss_words():
    _answer(403, {"error": "PLAN_REQUIRED", "message": "Feature services need another plan."})
    refused = raises(lambda: api.start_feature_service(_Session(), "org-1", "f", "l", "t"), api.ApiError)
    check(refused.message == "Feature services need another plan." and not refused.needs_sign_in, refused.message)
    _answer(422, {"error": "FEATURE_PUBLICATION_REFUSED", "message": "Too many features."})
    refused = raises(lambda: api.preview_feature_service(_Session(), "org-1", "f"), api.ApiError)
    check(refused.message == "Too many features." and refused.code == "FEATURE_PUBLICATION_REFUSED", refused.message)
    raises(lambda: api.feature_service_job(_Session(token=None), "org-1", "j"), api.ApiError)


# -- the batch -------------------------------------------------------------------


def test_vector_layers_become_new_services_and_the_button_says_so():
    link = memory.Link("old-item", "org-1")
    rows = [
        batch.Row("v1", "Parcels", "vector", link=link),
        batch.Row("r1", "Elevation", "raster", link=link),
        batch.Row("v2", "Roads", "vector"),
    ]
    plans = batch.plan(rows, ["v1", "r1", "v2"], org_id="org-1", as_service=True)
    check([p.as_service for p in plans] == [True, False, True], "only vector layers become services")
    check(plans[0].replace is None, "a service is always a new item, never a replacement")
    check(plans[1].replace is link, "a raster in the same click is replaced as it would be")
    check(batch.describe_plan(plans) == "Publish 3 layers (2 as feature services, 1 replaced)", batch.describe_plan(plans))
    check(batch.describe_plan(plans[:1]) == "Publish 1 layer as a feature service", batch.describe_plan(plans[:1]))
    check(batch.describe_plan([plans[0], plans[2]]) == "Publish 2 layers as feature services", "all services")

    without = batch.plan(rows, ["v1"], org_id="org-1")
    check(not without[0].as_service and without[0].replace is link, "unticked, nothing changes")
    check(batch.describe_plan(without) == "Replace 1 layer", batch.describe_plan(without))


def test_a_service_keeps_an_epsg_system_and_converts_a_custom_one():
    check(exporter.service_crs_plan("EPSG:2154") == exporter.KEEP, "Lambert 93 is stored as it is")
    check(exporter.service_crs_plan("EPSG:4326") == exporter.KEEP, "WGS 84 too")
    check(exporter.service_crs_plan("USER:100001") == exporter.TRANSFORM, "a custom system has no code to store")
    check(exporter.service_crs_plan("", valid=False) == exporter.REFUSE, "no system: refused as for any publish")


def main():
    tests = [
        test_picks_the_one_layer_or_says_why_not,
        test_follows_the_job_to_its_end_with_its_progress,
        test_a_failed_job_speaks_in_alignss_words,
        test_a_cancelled_follow_stops_polling_and_a_job_that_never_ends_is_told,
        test_describes_the_result,
        test_the_outcome_links_the_service_not_the_file,
        test_the_oapif_source_quotes_every_value,
        test_the_oauth2_configuration_is_pkce_features_only_and_secret_free,
        test_starts_the_service_and_removes_the_file_by_default,
        test_a_token_without_the_features_scopes_asks_to_sign_in_again,
        test_the_refusals_that_say_what_to_do_keep_alignss_words,
        test_vector_layers_become_new_services_and_the_button_says_so,
        test_a_service_keeps_an_epsg_system_and_converts_a_custom_one,
    ]
    for test in tests:
        test()
        print("ok  {}".format(test.__name__))
    print("{} checks passed".format(len(tests)))


if __name__ == "__main__":
    main()
