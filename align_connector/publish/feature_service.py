# -*- coding: utf-8 -*-
"""Publishing layers as editable feature services (ADR-0063 §3, TCK-2320, TCK-2346).

Each layer is sent to the Datastore in its own file, as for any publish;
ALIGN then turns the files into feature services — the features move into
the organization's database, where QGIS edits them live through OGC API
Features. One request asks for every service of a lot: the checked layers as
one service of several layers, or one service per layer (Benjamin,
2026-10-20). This module holds the decisions of that second step, free of
QGIS so they are testable on a plain interpreter:

* which layer of each uploaded file ALIGN will publish, or why it will not;
* the services of the request, and what a refused layer does to them;
* following ALIGN's background jobs to their end, honouring a cancellation;
* what to tell the user about the result, layer by layer;
* the data source QGIS opens a service layer with: no credentials in it, the
  plugin signs its requests (TCK-2348, ``auth/request_signer``).

The server decides everything that matters (the plan, the seat, the room, the
file); this reads its answers.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import html

from ..i18n import tr
from .notices import notice_texts


class ServicePublishFailed(Exception):
    """A step of the service publication ALIGN refused or could not finish."""


class ServiceStillPreparing(ServicePublishFailed):
    """The job was still running at the last poll: the service will appear."""


#: Job states that are over.
_TERMINAL = ("ready", "failed")

#: What came of one service of a request.
READY = "ready"
FAILED = "failed"
#: The follow was cancelled, or ALIGN is still preparing it: it will appear.
PENDING = "pending"
#: ALIGN refused its layer before the request: nothing was asked for it.
REFUSED = "refused"


class PublishedLayer(object):
    """A layer of a service, and the local layer it was published from."""

    def __init__(self, local_layer_id, layer_id, title, srid=None):
        self.local_layer_id = local_layer_id
        self.layer_id = layer_id
        self.title = title
        self.srid = srid


class PublishedService(object):
    """One service of a request, and what came of it.

    :param state: ``READY``, ``FAILED``, ``PENDING`` or ``REFUSED``
    :param result: ALIGN's result, when ready
    :param reason: why it failed or was refused, in ALIGN's words
    :param layers: its :class:`PublishedLayer` entries, when ready
    """

    def __init__(self, title, state, service_id=None, result=None, reason=None, layers=()):
        self.title = title
        self.state = state
        self.service_id = service_id
        self.result = result
        self.reason = reason
        self.layers = list(layers)


def pick_layer(preview):
    """The layer of the uploaded file ALIGN will publish.

    The plugin sends one layer per file, so the file holds exactly one; a
    refusal ALIGN gives for it (types mixed, too many features) is the user's
    message.

    :param preview: ALIGN's answer to the preview, ``{"layers": [...]}``
    :returns: the layer entry
    :raises ServicePublishFailed: when there is no layer, or it is refused
    """
    layers = preview.get("layers") if isinstance(preview, dict) else None
    if not isinstance(layers, list) or not layers or not isinstance(layers[0], dict):
        raise ServicePublishFailed(tr("ALIGN found no layer to publish in the file it received."))
    layer = layers[0]
    if layer.get("refusal"):
        raise ServicePublishFailed(str(layer["refusal"]))
    return layer


def service_request(title, layers):
    """One service of a publication request (TCK-2346).

    :param title: the service's name
    :param layers: ``[(asset_id, layer_name, layer_title)]``, in the order the
        service lists them. A service of one layer gives that layer its own
        name, so the layer carries no title of its own.
    """
    named = len(layers) > 1
    entries = []
    for asset_id, layer_name, layer_title in layers:
        entry = {"assetId": asset_id, "layerName": layer_name}
        if named:
            entry["title"] = layer_title
        entries.append(entry)
    return {"title": title, "layers": entries}


def plan_services(entries, preview, service_title=None):
    """The services one request asks for: with ``service_title``, every entry
    as a layer of that one service; without, one service per entry, under the
    entry's title.

    The file of each entry holds one layer, which ALIGN previews. A layer it
    refuses stops a service of several layers: nothing is published, and the
    message names the layer (TCK-2346, CA-3). With one service per layer, the
    others go on, and the refused one is reported.

    :param entries: ``[(local_layer_id, asset_id, title)]``, in the panel's order
    :param preview: ``asset_id -> preview``; raises :class:`ServicePublishFailed`
        for a refusal about the layer itself
    :returns: ``(services, refused)``: the ``services`` of the request, and a
        :class:`PublishedService` for each refused layer
    :raises ServicePublishFailed: a layer of the one service is refused
    """
    picked = []
    refused = []
    for _local_layer_id, asset_id, title in entries:
        try:
            layer = pick_layer(preview(asset_id))
        except ServicePublishFailed as exc:
            if service_title is not None:
                raise ServicePublishFailed(tr("“{}” cannot be a layer of the feature service: {}").format(title, exc))
            refused.append(PublishedService(title, REFUSED, reason=str(exc)))
            continue
        picked.append((asset_id, layer.get("name") or "", title))
    if service_title is not None:
        return [service_request(service_title, picked)], refused
    return [service_request(layer[2], [layer]) for layer in picked], refused


def follow(fetch, wait, cancelled, on_progress=None, max_polls=1200):
    """Poll a publication job until it is over.

    :param fetch: ``() -> job``, ALIGN's current view of the job
    :param wait: ``() -> None``, the pause between two polls
    :param cancelled: ``() -> bool``; a cancelled follow stops polling (the job
        itself goes on server-side: the service still appears when it is ready)
    :param on_progress: ``(loaded, total) -> None``
    :returns: the last job, ``None`` when the follow was cancelled
    :raises ServicePublishFailed: when ALIGN reports the job failed
    :raises ServiceStillPreparing: when the job is still running at the last poll
    """
    for _ in range(max_polls):
        if cancelled():
            return None
        job = fetch()
        progress = job.get("progress") if isinstance(job, dict) else None
        if on_progress is not None and isinstance(progress, dict):
            on_progress(int(progress.get("loaded") or 0), progress.get("total"))
        status = job.get("status") if isinstance(job, dict) else None
        if status == "failed":
            raise ServicePublishFailed(str(job.get("error") or tr("ALIGN could not publish the feature service.")))
        if status in _TERMINAL:
            return job
        wait()
    raise ServiceStillPreparing(tr("The feature service is still being prepared; it will appear in your Datastore when it is ready."))


def follow_jobs(jobs, fetch, wait, cancelled, on_progress=None, max_polls=1200):
    """Follow each job of a request to its end, one after the other: ALIGN
    builds an organization's services one at a time.

    A generator: what is yielded is known, even when a later poll fails (a
    session that expires mid-way); the jobs not yet followed go on server-side.

    :param jobs: the jobs ALIGN started, in the order of the request
    :param fetch: ``job_id -> job``
    :param on_progress: ``(share) -> None``, the share of the request done, 0 to 1
    :yields: ``(state, job, reason)``, one per job: ``READY`` with the last job;
        ``FAILED`` with ALIGN's reason; ``PENDING`` once the follow is
        cancelled, or for a job still running after the last poll
    """
    count = len(jobs)
    for index, job in enumerate(jobs):
        if cancelled():
            yield PENDING, job, None
            continue

        def share(loaded, total, index=index):
            if on_progress is not None and total:
                on_progress((index + min(1.0, loaded / float(total))) / count)

        try:
            done = follow(lambda job_id=job.get("jobId"): fetch(job_id), wait, cancelled, share, max_polls=max_polls)
        except ServiceStillPreparing as exc:
            yield PENDING, job, str(exc)
            continue
        except ServicePublishFailed as exc:
            yield FAILED, job, str(exc)
            continue
        if on_progress is not None:
            on_progress((index + 1) / float(count))
        yield (PENDING, job, None) if done is None else (READY, done, None)


def published_service(title, state, job, reason, local_of):
    """A followed job as a :class:`PublishedService`, its layers mapped back
    to the local layers their files came from.

    :param local_of: ``{asset_id: local_layer_id}``
    """
    service_id = job.get("serviceId") if isinstance(job, dict) else None
    if state != READY:
        return PublishedService(title, state, service_id=service_id, reason=reason)
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    layers = [
        PublishedLayer(local_of.get(layer.get("sourceAssetId")), layer.get("layerId"), layer.get("title") or title, layer.get("storageSrid"))
        for layer in result.get("layers") or []
        if isinstance(layer, dict) and layer.get("layerId")
    ]
    return PublishedService(title, READY, service_id=service_id, result=result, layers=layers)


def _layers_of(result):
    layers = result.get("layers") if isinstance(result, dict) else None
    return [layer for layer in layers or [] if isinstance(layer, dict)]


def _layer_details(layer):
    """What one layer of a service says beyond its count: its storage
    projection (CA-4 of TCK-2346, layer by layer), repairs, and notices."""
    parts = []
    srid = layer.get("storageSrid")
    if srid:
        parts.append(tr("Stored in EPSG:{}.").format(srid))
    repaired = int(layer.get("repaired") or 0)
    if repaired:
        parts.append(tr("{} invalid geometries were repaired.").format(repaired))
    without = int(layer.get("withoutGeometry") or 0)
    if without:
        parts.append(tr("{} features have no geometry.").format(without))
    parts.extend(notice_texts(layer))
    return parts


def layer_summary(layer):
    """One layer, named: its count, then its details."""
    title = html.escape(str(layer.get("title") or layer.get("layerName") or ""), quote=False)
    parts = [tr("“{}”: {} features.").format(title, int(layer.get("loaded") or 0))]
    parts.extend(_layer_details(layer))
    return " ".join(parts)


def describe_result(result):
    """What a service holds, in the user's language: its count and details
    for a service of one layer, each layer by name for a service of several."""
    layers = _layers_of(result)
    if len(layers) > 1:
        parts = [tr("{} layers published as one editable feature service.").format(len(layers))]
        parts.extend(layer_summary(layer) for layer in layers)
    else:
        layer = layers[0] if layers else {}
        parts = [tr("{} features published as an editable feature service.").format(int(layer.get("loaded") or 0))]
        parts.extend(_layer_details(layer))
    parts.extend(notice_texts(result))
    return " ".join(parts)


def _quoted(value):
    return "'{}'".format(str(value).replace("\\", "\\\\").replace("'", "\\'"))


def oapif_source(ogc_url, layer_id, srid=None):
    """The data source of the service's layer for QGIS' OAPIF provider.

    No ``authcfg``, no token: the plugin signs the layer's requests while it
    runs (TCK-2348, CA-8), so the project holds nothing secret.

    :param ogc_url: the service's OGC landing page
    :param layer_id: the collection (the layer's id)
    :param srid: ask for the features in the storage projection (EPSG code)
    """
    parts = [
        "pagingEnabled='true'",
        "preferCoordinatesForWfsT11='false'",
        "restrictToRequestBBOX='1'",
        "typename={}".format(_quoted(layer_id)),
        "url={}".format(_quoted(ogc_url)),
    ]
    if srid:
        parts.append("srsname={}".format(_quoted("EPSG:{}".format(int(srid)))))
    return " ".join(parts)
