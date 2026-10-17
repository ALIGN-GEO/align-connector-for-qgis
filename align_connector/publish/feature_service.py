# -*- coding: utf-8 -*-
"""Publishing a layer as an editable feature service (ADR-0063 §3, TCK-2320).

The file is sent to the Datastore as for any publish; ALIGN then turns it into
a feature service — the features move into the organization's database, where
QGIS edits them live through OGC API Features. This module holds the decisions
of that second step, free of QGIS so they are testable on a plain interpreter:

* which layer of the uploaded file ALIGN will publish, or why it will not;
* following ALIGN's background job to its end, honouring a cancellation;
* what to tell the user about the result;
* the data source QGIS opens the service with, and the OAuth2 configuration
  that signs its requests.

The server decides everything that matters (the plan, the seat, the room, the
file); this reads its answers.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import json

from ..i18n import tr

#: QGIS' OAuth2 method signs with PKCE from 3.34 on; ALIGN accepts no other
#: public-client flow, so a layer can only be opened for editing from there.
MIN_QGIS_FOR_EDITING = 33400

#: ``QgsAuthOAuth2Config`` enum values, as serialized in its JSON.
_GRANT_PKCE = 3
_CONFIG_CUSTOM = 1
_ACCESS_HEADER = 0


class ServicePublishFailed(Exception):
    """A step of the service publication ALIGN refused or could not finish."""


#: Job states that are over.
_TERMINAL = ("ready", "failed")


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


def follow(fetch, wait, cancelled, on_progress=None, max_polls=1200):
    """Poll a publication job until it is over.

    :param fetch: ``() -> job``, ALIGN's current view of the job
    :param wait: ``() -> None``, the pause between two polls
    :param cancelled: ``() -> bool``; a cancelled follow stops polling (the job
        itself goes on server-side: the service still appears when it is ready)
    :param on_progress: ``(loaded, total) -> None``
    :returns: the last job, ``None`` when the follow was cancelled
    :raises ServicePublishFailed: when ALIGN reports the job failed, or it never ends
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
    raise ServicePublishFailed(tr("The feature service is still being prepared; it will appear in your Datastore when it is ready."))


def describe_result(result):
    """One sentence on what the service holds, in the user's language."""
    loaded = int(result.get("loaded") or 0)
    parts = [tr("{} features published as an editable feature service.").format(loaded)]
    srid = result.get("storageSrid")
    if srid:
        parts.append(tr("Stored in EPSG:{}.").format(srid))
    repaired = int(result.get("repaired") or 0)
    if repaired:
        parts.append(tr("{} invalid geometries were repaired.").format(repaired))
    without = int(result.get("withoutGeometry") or 0)
    if without:
        parts.append(tr("{} features have no geometry.").format(without))
    parts.extend(str(w) for w in (result.get("warnings") or []))
    return " ".join(parts)


def _quoted(value):
    return "'{}'".format(str(value).replace("\\", "\\\\").replace("'", "\\'"))


def oapif_source(ogc_url, layer_id, authcfg, srid=None):
    """The data source of the service's layer for QGIS' OAPIF provider.

    :param ogc_url: the service's OGC landing page
    :param layer_id: the collection (the layer's id)
    :param authcfg: the QGIS authentication configuration carrying the token
    :param srid: ask for the features in the storage projection (EPSG code)
    """
    parts = [
        "pagingEnabled='true'",
        "preferCoordinatesForWfsT11='false'",
        "restrictToRequestBBOX='1'",
        "typename={}".format(_quoted(layer_id)),
        "url={}".format(_quoted(ogc_url)),
        "authcfg={}".format(_quoted(authcfg)),
    ]
    if srid:
        parts.append("srsname={}".format(_quoted("EPSG:{}".format(int(srid)))))
    return " ".join(parts)


def can_edit_in_qgis(qgis_version_int):
    """Whether this QGIS can open a feature service with its own sign-in."""
    try:
        return int(qgis_version_int) >= MIN_QGIS_FOR_EDITING
    except (TypeError, ValueError):
        return False


def oauth2_settings(name, authorize_url, token_url, client_id, scopes, port=7070):
    """The configuration of QGIS' OAuth2 method that signs the service layers.

    QGIS runs the flow itself (authorization code with PKCE, on a loopback
    redirect RFC 8252 allows on any port) and refreshes the token as it needs:
    the layer keeps working after the hour of an access token, and when the
    project is reopened. Its grant is its own, separate from the plugin's, and
    narrower: features only. ``persistToken`` keeps the tokens in QGIS'
    encrypted credential store, so a restart does not ask again.

    :returns: the JSON text QGIS stores under ``oauth2config``
    """
    return json.dumps(
        {
            "accessMethod": _ACCESS_HEADER,
            "apiKey": "",
            "clientId": client_id,
            "clientSecret": "",
            "configType": _CONFIG_CUSTOM,
            "customHeader": "",
            "description": tr("Signs the requests of the ALIGN feature-service layers."),
            "grantFlow": _GRANT_PKCE,
            "id": "",
            "name": name,
            "password": "",
            "persistToken": True,
            "queryPairs": {},
            "redirectPort": int(port),
            "redirectUrl": "",
            "refreshTokenUrl": token_url,
            "requestTimeout": 30,
            "requestUrl": authorize_url,
            "scope": " ".join(scopes),
            "tokenUrl": token_url,
            "username": "",
            "version": 1,
        },
        sort_keys=True,
    )
