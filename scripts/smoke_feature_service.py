#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove the decisions of publishing a layer as an editable feature service.

    python scripts/smoke_feature_service.py

Runs on a plain interpreter — no QGIS, no network. The transport is replaced
by canned answers; what is under test (TCK-2320, ADR-0063 §3):

* which layer of the uploaded file ALIGN is asked to publish, or why not;
* the services of one request (TCK-2346): the checked layers as one service,
  all or nothing, or one service per layer, a refused one reported;
* following ALIGN's jobs to their end, a failure in ALIGN's words, a cancelled
  follow that leaves the jobs running, a job that never ends;
* what the user is told, layer by layer, and which item the "Open in ALIGN"
  link points to;
* the OAPIF data source: no ``authcfg`` and no token, since the plugin signs
  the layer's requests itself (TCK-2348);
* the three API calls and their refusals (a token from before the
  ``features`` scopes asks to sign in again);
* the batch: vector layers become new services, never a replacement, and the
  button says so; the coordinate system a service keeps.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import qgis_stubs  # noqa: E402

qgis_stubs.install()

from align_connector import api, net  # noqa: E402
from align_connector.panel import batch  # noqa: E402
from align_connector.panel.outcome import INFO, SUCCESS, WARNING, outcome_of, services_outcome  # noqa: E402
from align_connector.publish import exporter, feature_service, memory  # noqa: E402
from align_connector.publish.feature_service import (  # noqa: E402
    FAILED,
    PENDING,
    READY,
    REFUSED,
    PublishedService,
    ServicePublishFailed,
)


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


# -- the services of a request (TCK-2346) -------------------------------------


def _previews(**refusals):
    """A preview per file: one layer named after the file, refused when asked."""

    def preview(asset_id):
        layer = {"name": "layer-of-" + asset_id}
        if asset_id in refusals:
            layer["refusal"] = refusals[asset_id]
        return {"layers": [layer]}

    return preview


ENTRIES = [("local-1", "file-1", "Parcels"), ("local-2", "file-2", "Water points"), ("local-3", "file-3", "Composters")]


def test_the_checked_layers_make_one_service_each_layer_named():
    services, refused = feature_service.plan_services(ENTRIES, _previews(), service_title="Jardins partagés")
    check(refused == [], "nothing refused")
    check(len(services) == 1 and services[0]["title"] == "Jardins partagés", services)
    check(
        services[0]["layers"]
        == [
            {"assetId": "file-1", "layerName": "layer-of-file-1", "title": "Parcels"},
            {"assetId": "file-2", "layerName": "layer-of-file-2", "title": "Water points"},
            {"assetId": "file-3", "layerName": "layer-of-file-3", "title": "Composters"},
        ],
        "CA-1: one service, its layers in the panel's order, each under its own name",
    )


def test_one_service_per_layer_by_default_its_layer_named_by_the_service():
    services, _ = feature_service.plan_services(ENTRIES[:2], _previews())
    check([service["title"] for service in services] == ["Parcels", "Water points"], services)
    check(services[0]["layers"] == [{"assetId": "file-1", "layerName": "layer-of-file-1"}], "a service of one layer: no layer title")


def test_a_refused_layer_stops_the_one_service_and_is_named():
    failed = raises(
        lambda: feature_service.plan_services(ENTRIES, _previews(**{"file-2": "The layer mixes points and lines."}), "Jardins"),
        ServicePublishFailed,
    )
    check(str(failed) == "“Water points” cannot be a layer of the feature service: The layer mixes points and lines.", str(failed))


def test_with_one_service_per_layer_a_refused_layer_is_reported_and_the_others_go():
    services, refused = feature_service.plan_services(ENTRIES, _previews(**{"file-2": "Too many features."}))
    check([service["title"] for service in services] == ["Parcels", "Composters"], "the others go (edge)")
    check(len(refused) == 1 and refused[0].state == REFUSED and refused[0].title == "Water points", vars(refused[0]))
    check(refused[0].reason == "Too many features.", refused[0].reason)


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


def _job_answers(answers):
    """``fetch(job_id)`` from a list of answers per job."""
    queues = {job_id: list(states) for job_id, states in answers.items()}
    return lambda job_id: queues[job_id].pop(0)


def test_follows_each_job_of_the_request_and_a_failed_one_does_not_stop_the_others():
    fetch = _job_answers(
        {
            "j1": [{"status": "running", "progress": {"loaded": 5, "total": 10}}, {"status": "ready", "serviceId": "s1", "result": {}}],
            "j2": [{"status": "failed", "error": "The file holds no feature."}],
            "j3": [{"status": "ready", "serviceId": "s3", "result": {}}],
        }
    )
    shares = []
    followed = list(
        feature_service.follow_jobs(
            [{"jobId": "j1"}, {"jobId": "j2"}, {"jobId": "j3"}], fetch, lambda: None, lambda: False, on_progress=shares.append
        )
    )
    check([state for state, _job, _reason in followed] == [READY, FAILED, READY], followed)
    check(followed[1][2] == "The file holds no feature.", "ALIGN's reason, for that service only")
    check(followed[0][1]["serviceId"] == "s1", "the last view of the job")
    check(shares[0] == 0.5 / 3 and shares[-1] == 1.0, shares)


def test_a_cancelled_follow_leaves_the_jobs_pending_and_a_long_one_is_pending_too():
    followed = list(feature_service.follow_jobs([{"jobId": "j1"}, {"jobId": "j2"}], None, lambda: None, lambda: True))
    check([state for state, _job, _reason in followed] == [PENDING, PENDING], "nothing polled once cancelled (edge)")

    def running(_job_id):
        return {"status": "running"}

    followed = list(feature_service.follow_jobs([{"jobId": "j1"}], running, lambda: None, lambda: False, max_polls=2))
    check(followed[0][0] == PENDING and "will appear in your Datastore" in followed[0][2], followed)


def test_what_was_followed_is_known_when_a_later_poll_fails():
    def fetch(job_id):
        if job_id == "j2":
            raise api.ApiError("Your ALIGN session expired.", needs_sign_in=True)
        return {"status": "ready"}

    seen = []
    try:
        for entry in feature_service.follow_jobs([{"jobId": "j1"}, {"jobId": "j2"}], fetch, lambda: None, lambda: False):
            seen.append(entry)
    except api.ApiError:
        pass
    check([state for state, _job, _reason in seen] == [READY], "the first service is known ready (edge)")


def test_a_ready_service_maps_its_layers_back_to_the_local_layers():
    job = {
        "serviceId": "svc-1",
        "result": {
            "layers": [
                {"layerId": "l1", "title": "Parcels", "sourceAssetId": "file-1", "storageSrid": 2154},
                {"layerId": "l2", "title": "Water points", "sourceAssetId": "file-2", "storageSrid": 4326},
            ]
        },
    }
    service = feature_service.published_service("Jardins", READY, job, None, {"file-1": "local-1", "file-2": "local-2"})
    check(service.service_id == "svc-1" and service.state == READY, vars(service))
    check([(layer.local_layer_id, layer.layer_id, layer.srid) for layer in service.layers] == [("local-1", "l1", 2154), ("local-2", "l2", 4326)], "CA-4, CA-5")
    failed = feature_service.published_service("Jardins", FAILED, {"serviceId": "svc-2"}, "No room.", {})
    check(failed.layers == [] and failed.reason == "No room.", vars(failed))


# -- what the user is told -----------------------------------------------------


def _layer(title, loaded, srid=None, **more):
    return dict({"title": title, "loaded": loaded, "storageSrid": srid, "notices": []}, **more)


def test_describes_the_result():
    text = feature_service.describe_result(
        {"layers": [_layer("Parcels", 1200, 2154, repaired=3, withoutGeometry=1, notices=[], warnings=["Field \"é\" renamed e."])], "notices": []}
    )
    check(text.startswith("1200 features published as an editable feature service."), text)
    check("EPSG:2154" in text and "3 invalid geometries" in text and "1 features have no geometry" in text, text)
    check(
        feature_service.describe_result({"layers": [_layer("Parcels", 5)], "notices": []}) == "5 features published as an editable feature service.",
        "only what happened",
    )


def test_describes_each_layer_of_a_service_of_several_by_name():
    text = feature_service.describe_result(
        {
            "layers": [_layer("Parcels", 120, 2154), _layer("Water points", 14, 4326), _layer("<b>Composters</b>", 9, 2154)],
            "notices": [{"code": "countsIncomplete", "params": {}, "message": "x"}],
        }
    )
    check(text.startswith("3 layers published as one editable feature service."), text)
    check("“Parcels”: 120 features. Stored in EPSG:2154." in text, "CA-4: each layer its own projection")
    check("“Water points”: 14 features. Stored in EPSG:4326." in text, text)
    check("<b>" not in text and "&lt;b&gt;Composters" in text, "a layer's name is escaped: the bar shows rich text (edge)")
    check(text.endswith("the counts shown may be incomplete."), "the service's own notices, last")


class _Task(object):
    def __init__(self, **values):
        self.asset_name = "Parcels"
        self.org_name = "Ville"
        self.as_service = True
        self.joins_service = None
        self.asset_id = "file-1"
        for key, value in values.items():
            setattr(self, key, value)


def test_a_sent_file_says_its_service_comes_next_and_links_nothing():
    own = outcome_of(_Task(), True)
    check(own.level == INFO and own.asset_id is None, "the file goes once the service is ready: no link")
    check(own.text == "“Parcels” was sent to Ville; ALIGN prepares its feature service next.", own.text)
    joins = outcome_of(_Task(joins_service="Jardins"), True)
    check(joins.text == "“Parcels” was sent to Ville; it will be a layer of the feature service “Jardins”.", joins.text)


class _ServicesTask(object):
    def __init__(self, services, **values):
        self.org_name = "Ville"
        self.services = services
        self.was_cancelled = False
        self.error_message = None
        self.follow_error = None
        for key, value in values.items():
            setattr(self, key, value)


def _ready(title, service_id, *layers):
    return PublishedService(title, READY, service_id=service_id, result={"layers": list(layers), "notices": []})


def test_the_outcome_of_one_service_links_it():
    done = services_outcome(_ServicesTask([_ready("Jardins", "svc-1", _layer("Parcels", 3, 2154), _layer("Water points", 2, 2154))]), True)
    check(done.level == SUCCESS and done.asset_id == "svc-1", (done.level, done.asset_id))
    check(done.text.startswith("“Jardins” is now an editable feature service in Ville on ALIGN. 2 layers published as one"), done.text)

    pending = services_outcome(_ServicesTask([PublishedService("Jardins", PENDING, service_id="svc-1")]), True)
    check(pending.level == INFO and pending.asset_id is None and "still preparing" in pending.text, (pending.level, pending.text))


def test_the_outcome_of_several_services_names_each_and_what_failed():
    services = [
        _ready("Parcels", "svc-1", _layer("Parcels", 3, 2154)),
        _ready("Water points", "svc-2", _layer("Water points", 2, 4326)),
        PublishedService("Composters", FAILED, service_id="svc-3", reason="No room left."),
        PublishedService("Mixed", REFUSED, reason="The layer mixes points and lines."),
    ]
    done = services_outcome(_ServicesTask(services), True)
    check(done.level == WARNING and done.asset_id is None, "a failure is a warning; several services: no single link")
    check(done.text.startswith("2 editable feature services are ready in Ville on ALIGN."), done.text)
    check("“Parcels”: 3 features. Stored in EPSG:2154." in done.text and "“Water points”: 2 features. Stored in EPSG:4326." in done.text, done.text)
    check("“Composters” is stored in your Datastore, but it did not become a feature service: No room left." in done.text, done.text)
    check("“Mixed” is stored in your Datastore, but it did not become a feature service: The layer mixes" in done.text, done.text)

    expired = services_outcome(_ServicesTask(services[:1] + [PublishedService("Water points", PENDING)], follow_error="Your session expired."), True)
    check(expired.level == WARNING and expired.text.endswith("Your session expired."), "the follow stopped: said (edge)")
    check("1 editable feature service is ready" in expired.text and "still preparing the feature service" in expired.text, expired.text)


def test_the_outcome_of_a_request_that_failed_or_was_cancelled():
    failed = services_outcome(_ServicesTask([], error_message="The layers are stored in your Datastore, but…"), False)
    check(failed.level == WARNING and failed.text.startswith("The layers are stored"), failed.text)
    cancelled = services_outcome(_ServicesTask([], was_cancelled=True), False)
    check(cancelled.level == INFO and cancelled.text == "Publishing cancelled. No feature service was started.", cancelled.text)


# -- what QGIS opens --------------------------------------------------------------


def test_the_oapif_source_quotes_every_value_and_carries_no_credential():
    source = feature_service.oapif_source("https://api.example/ogc", "lay'er", srid=2154)
    check("typename='lay\\'er'" in source, source)
    check("url='https://api.example/ogc'" in source, source)
    check(source.endswith("srsname='EPSG:2154'"), source)
    # TCK-2348 (CA-8) — the plugin signs the layer's requests: the project holds no authcfg, no token.
    check("authcfg" not in source and "token" not in source.lower(), source)
    check("srsname" not in feature_service.oapif_source("https://x", "l"), "no projection asked when unknown")


# -- the API ---------------------------------------------------------------------


def test_starts_the_services_in_one_request_and_removes_the_files_by_default():
    services = [feature_service.service_request("Parcels", [("file-1", "parcels", "Parcels")])]
    calls = _answer(202, {"jobs": [{"jobId": "job-1", "serviceId": "svc-1", "status": "pending", "title": "Parcels"}]})
    jobs = api.start_feature_services(_Session(), "org-1", services, visibility="org")
    check([job["jobId"] for job in jobs] == ["job-1"], jobs)
    verb, url, payload, bearer = calls[0]
    check(verb == "POST" and url.endswith("/api/v1/orgs/org-1/feature-services/publications"), url)
    check(
        payload == {"services": [{"title": "Parcels", "layers": [{"assetId": "file-1", "layerName": "parcels"}]}], "keepSource": False, "visibility": "org"},
        payload,
    )
    check(bearer == "alg_at_test", "the token travels as bearer")

    for unexpected in ({"jobId": "job-1"}, {"jobs": []}, {"jobs": [{"status": "pending"}]}):
        _answer(202, unexpected)
        raises(lambda: api.start_feature_services(_Session(), "org-1", services), api.ApiError)

    calls = _answer(200, {"jobId": "job-1", "status": "running"})
    api.feature_service_job(_Session(), "org-1", "job-1")
    check(calls[0][0] == "GET" and calls[0][1].endswith("/feature-services/publications/job-1"), calls)

    calls = _answer(200, {"layers": []})
    api.preview_feature_service(_Session(), "org-1", "file-1")
    check(calls[0][1].endswith("/feature-services/publications/preview") and calls[0][2] == {"assetId": "file-1"}, calls)


def test_a_token_without_the_features_scopes_asks_to_sign_in_again():
    _answer(403, {"error": "INSUFFICIENT_SCOPE", "message": "Missing scope features:write"})
    refused = raises(lambda: api.start_feature_services(_Session(), "org-1", []), api.ApiError)
    check(refused.needs_sign_in and "Sign in to ALIGN again" in refused.message, refused.message)


def test_the_refusals_that_say_what_to_do_keep_alignss_words():
    _answer(403, {"error": "PLAN_REQUIRED", "message": "Feature services need another plan."})
    refused = raises(lambda: api.start_feature_services(_Session(), "org-1", []), api.ApiError)
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


class _Request(object):
    def __init__(self, plan):
        self.plan = plan


def test_the_checked_layers_as_one_service_and_the_button_says_so():
    link = memory.Link("old-item", "org-1")
    rows = [
        batch.Row("v1", "Parcels", "vector"),
        batch.Row("r1", "Elevation", "raster", link=link),
        batch.Row("v2", "Roads", "vector"),
    ]
    plans = batch.plan(rows, ["v1", "r1", "v2"], org_id="org-1", as_service=True)
    vectors = [plans[0], plans[2]]
    check(batch.describe_plan(vectors, one_service=True) == "Publish 2 layers as one feature service", batch.describe_plan(vectors, one_service=True))
    check(batch.describe_plan(plans, one_service=True) == "Publish 3 layers (2 in one feature service, 1 replaced)", batch.describe_plan(plans, one_service=True))
    check(batch.describe_plan(plans[:1], one_service=True) == "Publish 1 layer as a feature service", "one layer is its own service (edge)")

    lot = batch.Batch({"id": "org-1"}, "org", None, None, [_Request(p) for p in plans], service_title="Jardins")
    check(lot.one_service_title == "Jardins" and [r.plan.layer_id for r in lot.service_requests] == ["v1", "v2"], "only the vector layers")
    alone = batch.Batch({"id": "org-1"}, "org", None, None, [_Request(plans[0])], service_title="Jardins")
    check(alone.one_service_title is None, "a single layer is its own service, under its own name (edge)")


def test_a_service_keeps_an_epsg_system_and_converts_a_custom_one():
    check(exporter.service_crs_plan("EPSG:2154") == exporter.KEEP, "Lambert 93 is stored as it is")
    check(exporter.service_crs_plan("EPSG:4326") == exporter.KEEP, "WGS 84 too")
    check(exporter.service_crs_plan("USER:100001") == exporter.TRANSFORM, "a custom system has no code to store")
    check(exporter.service_crs_plan("", valid=False) == exporter.REFUSE, "no system: refused as for any publish")


def main():
    tests = [
        test_picks_the_one_layer_or_says_why_not,
        test_the_checked_layers_make_one_service_each_layer_named,
        test_one_service_per_layer_by_default_its_layer_named_by_the_service,
        test_a_refused_layer_stops_the_one_service_and_is_named,
        test_with_one_service_per_layer_a_refused_layer_is_reported_and_the_others_go,
        test_follows_the_job_to_its_end_with_its_progress,
        test_a_failed_job_speaks_in_alignss_words,
        test_a_cancelled_follow_stops_polling_and_a_job_that_never_ends_is_told,
        test_follows_each_job_of_the_request_and_a_failed_one_does_not_stop_the_others,
        test_a_cancelled_follow_leaves_the_jobs_pending_and_a_long_one_is_pending_too,
        test_what_was_followed_is_known_when_a_later_poll_fails,
        test_a_ready_service_maps_its_layers_back_to_the_local_layers,
        test_describes_the_result,
        test_describes_each_layer_of_a_service_of_several_by_name,
        test_a_sent_file_says_its_service_comes_next_and_links_nothing,
        test_the_outcome_of_one_service_links_it,
        test_the_outcome_of_several_services_names_each_and_what_failed,
        test_the_outcome_of_a_request_that_failed_or_was_cancelled,
        test_the_oapif_source_quotes_every_value_and_carries_no_credential,
        test_starts_the_services_in_one_request_and_removes_the_files_by_default,
        test_a_token_without_the_features_scopes_asks_to_sign_in_again,
        test_the_refusals_that_say_what_to_do_keep_alignss_words,
        test_vector_layers_become_new_services_and_the_button_says_so,
        test_the_checked_layers_as_one_service_and_the_button_says_so,
        test_a_service_keeps_an_epsg_system_and_converts_a_custom_one,
    ]
    for test in tests:
        test()
        print("ok  {}".format(test.__name__))
    print("{} checks passed".format(len(tests)))


if __name__ == "__main__":
    main()
