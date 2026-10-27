#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The task that asks ALIGN for the feature services of a lot (TCK-2346).

    python scripts/smoke_services_task_qgis.py   (with QGIS' Python)

The task runs on its own thread in QGIS; here ``run()`` is called directly,
with ALIGN's three answers (preview, start, job) replaced. What is proven:

* the checked layers as one service: one request, one service, each layer
  under its own name, each mapped back to its local layer (CA-1, CA-5);
* a layer ALIGN refuses stops that one service before anything is asked,
  and the message names it (CA-3);
* one service per layer: one request for them all; a refused layer is
  reported, the others are published;
* a session that expires mid-follow keeps what was followed, and the rest is
  told as still being prepared; a refusal at the start says so; a task
  cancelled before the request asks for nothing.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import Qgis, QgsApplication  # noqa: E402


def check(condition, message):
    if not condition:
        raise AssertionError(message)


ENTRIES = [("local-1", "file-1", "Parcels"), ("local-2", "file-2", "Water points"), ("local-3", "file-3", "Composters")]


class _Align(object):
    """ALIGN's answers: a preview per file, the jobs of a request, each job's states."""

    def __init__(self, api, refusals=None, expire_on=None, start_refusal=None):
        self.api = api
        self.refusals = refusals or {}
        self.expire_on = expire_on
        self.start_refusal = start_refusal
        self.requests = []

    def preview(self, _session, _org_id, asset_id):
        if asset_id in self.refusals:
            raise self.api.ApiError(self.refusals[asset_id], status=422, code="FEATURE_PUBLICATION_REFUSED")
        return {"layers": [{"name": "layer-of-" + asset_id}]}

    def start(self, _session, _org_id, services, visibility=None, keep_source=False):
        if self.start_refusal is not None:
            raise self.start_refusal
        self.requests.append({"services": services, "visibility": visibility, "keepSource": keep_source})
        return [{"jobId": "job-{}".format(index), "serviceId": "svc-{}".format(index), "status": "pending"} for index, _ in enumerate(services)]

    def job(self, _session, _org_id, job_id):
        if job_id == self.expire_on:
            raise self.api.ApiError("Your ALIGN session expired while the feature service was being prepared.", needs_sign_in=True)
        index = int(job_id.split("-")[1])
        asked = self.requests[-1]["services"][index]
        layers = [
            {"layerId": "{}-{}".format(job_id, n), "title": layer.get("title") or asked["title"], "sourceAssetId": layer["assetId"], "loaded": 3, "storageSrid": 2154}
            for n, layer in enumerate(asked["layers"])
        ]
        return {"jobId": job_id, "serviceId": "svc-{}".format(index), "status": "ready", "result": {"layers": layers, "notices": []}}


def _run(module, align, entries, service_title=None, cancel=False):
    module.api.preview_feature_service = align.preview
    module.api.start_feature_services = align.start
    module.api.feature_service_job = align.job
    task = module.PublishServicesTask(object(), "org-1", "Ville", entries, service_title=service_title, visibility="org")
    if cancel:
        task.cancel()
    return task, task.run()


def main():
    app = QgsApplication([], False)
    app.initQgis()
    from align_connector import api
    from align_connector.publish import service_task
    from align_connector.publish.feature_service import PENDING, READY, REFUSED

    service_task._POLL_SECONDS = 0

    align = _Align(api)
    task, ok = _run(service_task, align, ENTRIES, service_title="Jardins partagés")
    check(ok and task.asset_name == "Feature service “Jardins partagés”", (ok, task.error_message, task.asset_name))
    check(len(align.requests) == 1 and len(align.requests[0]["services"]) == 1, "one request, one service (CA-1)")
    asked = align.requests[0]["services"][0]
    check([layer["title"] for layer in asked["layers"]] == ["Parcels", "Water points", "Composters"], asked)
    check(align.requests[0]["keepSource"] is False and align.requests[0]["visibility"] == "org", "the files go once it is ready")
    service = task.services[0]
    check(service.state == READY and service.service_id == "svc-0", vars(service))
    check([layer.local_layer_id for layer in service.layers] == ["local-1", "local-2", "local-3"], "each layer to its original (CA-5)")

    align = _Align(api, refusals={"file-2": "The layer mixes points and lines."})
    task, ok = _run(service_task, align, ENTRIES, service_title="Jardins partagés")
    check(not ok and align.requests == [], "a refused layer: nothing asked (CA-3)")
    check("“Water points” cannot be a layer of the feature service: The layer mixes points and lines." in task.error_message, task.error_message)
    check(task.error_message.startswith("The layers are stored in your Datastore, but they did not become the feature service “Jardins partagés”"), task.error_message)

    task, ok = _run(service_task, align, ENTRIES)
    check(ok and task.asset_name == "3 feature services", (ok, task.asset_name))
    check(len(align.requests) == 1 and [s["title"] for s in align.requests[0]["services"]] == ["Parcels", "Composters"], "one request for them all")
    check([s.state for s in task.services] == [READY, READY, REFUSED], [vars(s) for s in task.services])
    check(task.services[2].reason == "The layer mixes points and lines.", "the refused one, with ALIGN's reason")

    align = _Align(api, expire_on="job-1")
    task, ok = _run(service_task, align, ENTRIES)
    check(ok and [s.state for s in task.services] == [READY, PENDING, PENDING], [vars(s) for s in task.services])
    check(task.needs_sign_in and "session expired" in task.follow_error, "the follow stopped, and says why (edge)")

    align = _Align(api, start_refusal=api.ApiError("Your organization has no room left.", status=413))
    task, ok = _run(service_task, align, ENTRIES[:1])
    check(not ok and task.error_status == 413, (ok, task.error_status))
    check(task.error_message.startswith("“Parcels” is stored in your Datastore, but it did not become a feature service:"), task.error_message)

    align = _Align(api)
    task, ok = _run(service_task, align, ENTRIES, cancel=True)
    check(not ok and task.was_cancelled and align.requests == [], "cancelled before the request: nothing asked (edge)")

    print("services task inside QGIS {}: all checks passed".format(Qgis.QGIS_VERSION))
    app.exitQgis()


if __name__ == "__main__":
    main()
