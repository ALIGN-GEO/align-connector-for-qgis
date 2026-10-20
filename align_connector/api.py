# -*- coding: utf-8 -*-
"""The ALIGN API, as this plugin uses it.

One module, so that every call carries the token the same way and every refusal
is turned into the same kind of answer. The rule throughout: **the server
decides, the plugin reports**. Nothing here re-implements an authorisation
check — an organization the user cannot publish to is refused by ALIGN, and
that refusal is what the user is shown.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from . import config
from . import net
from .i18n import tr


class ApiError(Exception):
    """A refusal, already phrased for a human.

    :param message: what to show
    :param needs_sign_in: the session is gone; the caller should offer to reconnect
    :param status: the HTTP status behind the refusal, when there was one. Lets a
        caller recognise the two refusals that are about capacity (402 credits,
        413 storage) without parsing the sentence (TCK-2179).
    """

    def __init__(self, message, needs_sign_in=False, status=None, code=None, retry_after=None):
        super().__init__(message)
        self.message = message
        #: Seconds ALIGN asked to wait before asking again (a 429's
        #: ``Retry-After``), or None (TCK-2296, defect 69).
        self.retry_after = retry_after
        #: The server's own refusal code, when it sent one (TCK-2218): lets a
        #: caller tell "the item is gone" from "you may not", both 4xx.
        self.code = code
        self.needs_sign_in = needs_sign_in
        self.status = status


def _refusal(status):
    """What a refusal means to someone publishing a layer, in their language.

    A function, not a table: the sentences are translated when they are
    shown, after the translator is installed, never at import time
    (TCK-2183). ``None`` for a status the plugin has no words for.
    """
    known = {
        401: (tr("Your ALIGN session has expired. Sign in again to publish."), True),
        # TCK-2178 — the conversion this file needs (GeoTIFF → COG, MBTiles →
        # PMTiles) would cost more credits than the organization has. Refused
        # before the first byte; the numbers come from the body (see below).
        402: (tr("Your organization does not have enough credits to convert this file. "
                 "Add credits in Plans & Capacity, then publish again."), False),
        403: (tr("Your role in this organization does not allow publishing."), False),
        404: (tr("That organization could not be found, or you are no longer a member of it."), False),
        413: (tr("There is not enough space left in this organization's Datastore."), False),
        415: (tr("ALIGN does not accept this file type."), False),
        429: (tr("ALIGN is rate-limiting this client. Wait a moment and try again."), False),
    }
    return known.get(status)


def _credits_refusal(result):
    """The 402 composed from its structured fields, so the two numbers read
    in the user's language rather than in the server's English sentence."""
    details = result.body.get("details") if isinstance(result.body, dict) else None
    if not isinstance(details, dict):
        return None
    needed, available = details.get("estimatedCredits"), details.get("availableCredits")
    if not isinstance(needed, (int, float)) or not isinstance(available, (int, float)):
        return None
    return tr(
        "Converting this file needs {} credits; this organization has {}. "
        "Add credits in Plans & Capacity, then publish again."
    ).format(_whole(needed), _whole(available))


def _whole(number):
    return int(number) if float(number).is_integer() else number


def connector_not_allowed():
    """The organization's switch for the plugin is off, not the role
    (TCK-1666): its own sentence, in the user's language, with the remedy."""
    return tr(
        "This organization has not enabled the QGIS plugin for its members. "
        "An owner or admin can turn it on in Settings → Integrations."
    )


def _seconds(value):
    """A ``Retry-After`` in seconds, at least one; None for a date or nothing readable."""
    try:
        return max(1, int(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _raise_for(result, fallback):
    """Turn a failed response into an :class:`ApiError` a user can act on.

    A status the plugin knows gets the plugin's own sentence, in the user's
    language; the server's English text is only shown for a status the plugin
    has no words for (TCK-2183).
    """
    known = _refusal(result.status)
    if known:
        message, needs_sign_in = known
        if result.status == 402:
            message = _credits_refusal(result) or message
        elif result.status == 403 and result.code() == "QGIS_CONNECTOR_NOT_ALLOWED":
            message = connector_not_allowed()
        elif result.status == 415:
            # The server names the type it refused: worth more than our line.
            message = result.message(message)
        retry_after = _seconds(result.headers.get("retry-after")) if result.status == 429 else None
        raise ApiError(message, needs_sign_in=needs_sign_in, status=result.status, retry_after=retry_after)
    if result.status == net.NO_RESPONSE:
        raise ApiError(tr("ALIGN could not be reached. Check your connection and try again."))
    raise ApiError(result.message(fallback), status=result.status)


def list_organizations(session):
    """The organizations the signed-in user belongs to.

    :returns: list of ``{"id", "name", "role", "connector"}``, in the order ALIGN returns them
    :raises ApiError: when the call fails
    """
    token = session.access_token()
    if not token:
        raise ApiError(tr("You are not signed in to ALIGN."), needs_sign_in=True)

    result = net.get_json(config.orgs_endpoint(), bearer=token)
    if not result.ok:
        _raise_for(result, tr("Could not read your organizations"))

    items = result.body.get("items") if isinstance(result.body, dict) else None
    if not isinstance(items, list):
        raise ApiError(tr("ALIGN returned an unexpected answer while listing organizations."))

    # TCK-2348 (CA-17): ALIGN lists every organization, each saying whether it
    # opens itself to the plugin; an older ALIGN leaves out those that do not.
    return [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "role": item.get("role"),
            "connector": item.get("connector") if isinstance(item.get("connector"), dict) else None,
        }
        for item in items
        if isinstance(item, dict) and item.get("id")
    ]


def capacity(session, org_id):
    """What the organization can still spend here — storage and credits (TCK-2179).

    A read for the publisher about to publish. It carries no data and no
    secret: bytes used and allowed, and whole credits. ALIGN never says what a
    credit is worth; neither does this.

    :returns: ``{"storage": {"usedBytes", "limitBytes"}, "credits": {"available"},
        "featureServices"}``; `featureServices` says whether a layer may be
        published here as an editable feature service (TCK-2320), False from a
        server that does not say
    :raises ApiError: when the call fails
    """
    token = session.access_token()
    if not token:
        raise ApiError(tr("You are not signed in to ALIGN."), needs_sign_in=True)

    result = net.get_json(config.capacity_endpoint(org_id), bearer=token)
    if not result.ok:
        _raise_for(result, tr("Could not read what this organization can still spend"))

    body = result.body if isinstance(result.body, dict) else None
    storage = body.get("storage") if body else None
    credits = body.get("credits") if body else None
    if not isinstance(storage, dict) or not isinstance(credits, dict):
        raise ApiError(tr("ALIGN returned an unexpected answer while reading the organization's capacity."))
    return {
        "storage": {
            "usedBytes": _count(storage.get("usedBytes")),
            "limitBytes": _count(storage.get("limitBytes")),
        },
        "credits": {"available": _count(credits.get("available"))},
        "featureServices": body.get("featureServices") is True,
    }


def _count(value):
    """A non-negative integer, or None when the server did not say.

    The server's schema promises integers; a client still reads defensively,
    because the figure ends up in a sentence shown to a person.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0:
        return None
    return int(value)


def open_upload_session(session, org_id, filename, content_type, size_bytes, already_servable=False):
    """Open a resumable upload session for one file.

    Used for every publish, whatever the size: a small file simply travels in
    one chunk. One path, tested once, rather than two that drift.

    This is also where a publish that cannot succeed is stopped — **before any
    byte travels**. ALIGN checks the organization's remaining space here, and
    whether the file is within what it can convert. A refusal costs seconds
    instead of an hour.

    :param already_servable: the caller has VERIFIED the file needs no
        conversion (a 3857 COG). Never assert it without checking: the server
        re-verifies, and a wrong claim only routes the file back through the
        conversion it said it did not need.
    :returns: ``{"assetId", "protocol", "sessionUrl", "expiresAt", "chunkSizeBytes"}``
    """
    token = session.access_token()
    if not token:
        raise ApiError(tr("You are not signed in to ALIGN."), needs_sign_in=True)

    payload = {"filename": filename, "contentType": content_type, "sizeBytes": size_bytes}
    if already_servable:
        payload["alreadyServable"] = True

    result = net.post_json(
        config.upload_session_endpoint(org_id),
        payload,
        bearer=token,
    )
    if not result.ok:
        # Two very different refusals share the 413: no room left, and a file
        # beyond what ALIGN can prepare for display. The server distinguishes
        # them by code, and so must the message — buying more storage fixes the
        # first and changes nothing to the second.
        if result.code() == "DATASTORE_DERIVATION_TOO_LARGE":
            raise ApiError(result.message(tr("This file is too large for ALIGN to prepare for display.")))
        _raise_for(result, tr("ALIGN refused the upload"))

    body = result.body
    for key in ("assetId", "sessionUrl", "chunkSizeBytes"):
        if not body.get(key):
            raise ApiError(tr("ALIGN returned an incomplete upload session."))
    return body


def finalize(session, org_id, asset_id, filename, name, provenance=None, description=None, visibility=None,
             already_servable=False, style=None, iso=None):
    """Catalogue the asset once its bytes have landed.

    The declared size is not re-sent: ALIGN measures what actually arrived, and
    that measurement is the one that counts against the quota — and against the
    conversion budget, re-checked here on the real size (TCK-2178). A refusal at
    this point deletes the uploaded bytes; nothing is catalogued.

    :param already_servable: the same claim made at the upload session — ALIGN
        does not remember it between the two calls, so it is said again.
    :param style: the layer's symbology in ALIGN's canonical model (TCK-2201),
        which becomes the item's default style; None sends nothing.
    :param iso: the layer's metadata for the item's ISO sheet (TCK-2202), the
        fields the sheet edits; a field the organization enforces is kept as
        the organization has it, and the returned asset shows what was kept.
    """
    token = session.access_token()
    if not token:
        raise ApiError(tr("Your ALIGN session expired while the file was uploading."), needs_sign_in=True)

    payload = {"filename": filename, "name": name}
    if already_servable:
        payload["alreadyServable"] = True
    if provenance:
        payload["provenance"] = provenance
    # Sent in the same call that catalogues the bytes: a second call could fail
    # and leave the asset with a visibility nobody chose.
    if description:
        payload["description"] = description
    if visibility:
        payload["visibility"] = visibility
    if style:
        payload["style"] = style
    if iso:
        payload["iso"] = iso

    result = net.post_json(config.finalize_endpoint(org_id, asset_id), payload, bearer=token)
    if not result.ok:
        _raise_for(result, tr("ALIGN could not catalogue the uploaded file"))
    return result.body


#: The refusals a replacement can meet, by the server's code (TCK-2218): the
#: plugin's own sentence for each, with the remedy, which is always the same —
#: publish as a new layer.
_REPLACE_REFUSALS = {
    "DATASTORE_ASSET_NOT_FOUND": lambda: tr("The item this layer was published as no longer exists on ALIGN."),
    "DATASTORE_ACCESS_DENIED": lambda: tr(
        "Only the item's creator or an organization admin can replace it. Publish it as a new layer instead."
    ),
    "DATASTORE_REPLACE_CONFLICT": lambda: tr(
        "The item changed on ALIGN since this layer was last read. Nothing was replaced: "
        "reopen it from ALIGN, or publish as a new layer."
    ),
    "DATASTORE_UPLOAD_NOT_FOUND": lambda: tr("The transfer did not complete on ALIGN's side. Try again."),
    "DATASTORE_REPLACE_FORMAT_MISMATCH": lambda: tr(
        "The item on ALIGN is stored in another format than this export; a replacement must keep "
        "the format. Publish it as a new layer instead."
    ),
    "DATASTORE_REPLACE_NEEDS_DERIVATION": lambda: tr(
        "An item ALIGN converted for display (GeoTIFF, MBTiles) cannot be replaced in place yet. "
        "Publish it as a new layer instead."
    ),
    "DATASTORE_NOT_FILE_ASSET": lambda: tr(
        "The item this layer was published as is a reference, not a file: it has nothing to replace."
    ),
}


def _raise_for_replace(result, fallback):
    """A replacement's refusal, by code first, by status otherwise."""
    sentence = _REPLACE_REFUSALS.get(result.code() or "")
    if sentence is not None:
        raise ApiError(sentence(), status=result.status, code=result.code())
    _raise_for(result, fallback)


def open_replace_session(session, org_id, asset_id, filename, content_type, size_bytes):
    """Open the resumable session that replaces a published item's bytes (TCK-2218).

    ALIGN decides here, before any byte travels: the right to write this item,
    the format, the room. The answer carries the transit object to send to and
    the item's current generation, which the finalize cites so that a change
    made meanwhile by someone else is never overwritten.

    :returns: ``{"assetId", "transitFilename", "protocol", "sessionUrl", "expiresAt",
        "chunkSizeBytes", "baseGeneration"}``
    """
    token = session.access_token()
    if not token:
        raise ApiError(tr("You are not signed in to ALIGN."), needs_sign_in=True)

    payload = {"filename": filename, "contentType": content_type, "sizeBytes": size_bytes}
    result = net.post_json(config.replace_session_endpoint(org_id, asset_id), payload, bearer=token)
    if not result.ok:
        _raise_for_replace(result, tr("ALIGN refused the replacement"))

    body = result.body
    for key in ("transitFilename", "sessionUrl", "chunkSizeBytes", "baseGeneration"):
        if not body.get(key):
            raise ApiError(tr("ALIGN returned an incomplete upload session."))
    return body


def finalize_replace(session, org_id, asset_id, transit_filename, base_generation, name=None, style=None, iso=None):
    """Swap the transferred bytes in, under the generation read at open (TCK-2218).

    The previous version is kept on ALIGN; the style and the metadata sheet
    are replayed as at a first publish; the answer names what changed in the
    attribute schema when the format describes it.
    """
    token = session.access_token()
    if not token:
        raise ApiError(tr("Your ALIGN session expired while the file was uploading."), needs_sign_in=True)

    payload = {"transitFilename": transit_filename, "baseGeneration": base_generation}
    if name:
        payload["name"] = name
    if style:
        payload["style"] = style
    if iso:
        payload["iso"] = iso

    result = net.post_json(config.replace_finalize_endpoint(org_id, asset_id), payload, bearer=token)
    if not result.ok:
        _raise_for_replace(result, tr("ALIGN could not replace the item"))
    return result.body


def create_web_map(session, org_id, name, layers, map_view=None, description=None, visibility=None):
    """Ask ALIGN for the web map that shows published items (TCK-2204).

    :param layers: entries ``{"assetId", "title"?, "visible"?, "geometryType"?,
        "style"?}``, top of the map first (a legend's order)
    :returns: ``{"assetId", "name", "layers": [{assetId, layerId}],
        "skipped": [{assetId, reason}]}``
    :raises ApiError: when the call fails; ``status`` carries the refusal
    """
    token = session.access_token()
    if not token:
        raise ApiError(tr("You are not signed in to ALIGN."), needs_sign_in=True)

    payload = {"name": name, "layers": list(layers)}
    if map_view:
        payload["mapView"] = map_view
    if description:
        payload["description"] = description
    if visibility:
        payload["visibility"] = visibility

    result = net.post_json(config.web_map_endpoint(org_id), payload, bearer=token)
    if not result.ok:
        _raise_for(result, tr("Could not create the web map"))

    body = result.body if isinstance(result.body, dict) else None
    if not body or not body.get("assetId"):
        raise ApiError(tr("ALIGN returned an unexpected answer while creating the web map."))
    return {
        "assetId": body["assetId"],
        "name": body.get("name") or name,
        "layers": [e for e in (body.get("layers") or []) if isinstance(e, dict)],
        "skipped": [e for e in (body.get("skipped") or []) if isinstance(e, dict)],
    }


def create_reference(session, org_id, name, body, provenance=None, description=None, visibility=None):
    """Register (or find) a live reference to a web service (TCK-2207).

    :param body: ``{"kind", "url", …}`` as `publish.services.describe_service` builds it
    :returns: ``{"assetId", "created", "name", "gisFormat"}``
    :raises ApiError: when the call fails; ``status`` carries the refusal
    """
    token = session.access_token()
    if not token:
        raise ApiError(tr("You are not signed in to ALIGN."), needs_sign_in=True)

    payload = dict(body)
    payload["name"] = name
    if provenance:
        payload["provenance"] = provenance
    if description:
        payload["description"] = description
    if visibility:
        payload["visibility"] = visibility

    result = net.post_json(config.references_endpoint(org_id), payload, bearer=token)
    if not result.ok:
        _raise_for(result, tr("Could not register the reference"))

    answer = result.body if isinstance(result.body, dict) else None
    if not answer or not answer.get("assetId"):
        raise ApiError(tr("ALIGN returned an unexpected answer while registering the reference."))
    return {
        "assetId": answer["assetId"],
        "created": bool(answer.get("created", True)),
        "name": answer.get("name") or name,
        "gisFormat": answer.get("gisFormat"),
    }


def _feature_service_refusal(result, fallback):
    """A refusal of the feature-service doors (TCK-2320), in the user's words.

    A token from before this version carries no `features:*` scope: ALIGN
    answers INSUFFICIENT_SCOPE, and the remedy is to sign in again. The
    refusals that already say what to do (a layer that cannot be published,
    a plan without feature services) keep ALIGN's sentence.
    """
    if result.status == 403 and result.code() == "INSUFFICIENT_SCOPE":
        raise ApiError(
            tr("Sign in to ALIGN again to publish and edit feature services from QGIS."),
            needs_sign_in=True,
            status=403,
            code="INSUFFICIENT_SCOPE",
        )
    if result.status == 403 and result.code() == "PLAN_REQUIRED":
        raise ApiError(result.message(fallback), status=403, code="PLAN_REQUIRED")
    if result.status == 422 and result.code() == "FEATURE_PUBLICATION_REFUSED":
        raise ApiError(result.message(fallback), status=422, code="FEATURE_PUBLICATION_REFUSED")
    _raise_for(result, fallback)


def preview_feature_service(session, org_id, asset_id):
    """The layers of an uploaded file, as a feature service would hold them (TCK-2318).

    :returns: ``{"layers": [{name, featureCount, geometryType, srid, fields, warnings, refusal?}]}``
    """
    token = session.access_token()
    if not token:
        raise ApiError(tr("You are not signed in to ALIGN."), needs_sign_in=True)
    result = net.post_json(config.feature_publication_preview_endpoint(org_id), {"assetId": asset_id}, bearer=token)
    if not result.ok:
        _feature_service_refusal(result, tr("ALIGN could not read the layers of the file"))
    if not isinstance(result.body, dict):
        raise ApiError(tr("ALIGN returned an unexpected answer while reading the file."))
    return result.body


def start_feature_services(session, org_id, services, visibility=None, keep_source=False):
    """Ask ALIGN to publish uploaded files as feature services (TCK-2318, TCK-2346).

    One request for every service of a lot: ALIGN checks it whole before it
    starts anything, and starts one job per service. The files were sent for
    this purpose only, so by default ALIGN removes them once the services are
    ready (``keep_source``).

    :param services: ``[{title, layers: [{assetId, layerName, title?}]}]``
        (``feature_service.service_request``)
    :returns: the jobs, one per service in the order asked,
        ``[{"jobId", "serviceId", "status", "title", ...}]``
    """
    token = session.access_token()
    if not token:
        raise ApiError(tr("You are not signed in to ALIGN."), needs_sign_in=True)
    payload = {"services": list(services), "keepSource": bool(keep_source)}
    if visibility:
        payload["visibility"] = visibility
    result = net.post_json(config.feature_publications_endpoint(org_id), payload, bearer=token)
    if not result.ok:
        _feature_service_refusal(result, tr("ALIGN could not start the feature service"))
    jobs = result.body.get("jobs") if isinstance(result.body, dict) else None
    if (
        not isinstance(jobs, list)
        or len(jobs) != len(payload["services"])
        or not all(isinstance(job, dict) and job.get("jobId") for job in jobs)
    ):
        raise ApiError(tr("ALIGN returned an unexpected answer while starting the feature service."))
    return jobs


def feature_service_job(session, org_id, job_id):
    """ALIGN's current view of a publication job."""
    token = session.access_token()
    if not token:
        raise ApiError(tr("Your ALIGN session expired while the feature service was being prepared."), needs_sign_in=True)
    result = net.get_json(config.feature_publication_job_endpoint(org_id, job_id), bearer=token)
    if not result.ok:
        _feature_service_refusal(result, tr("Could not follow the feature service"))
    if not isinstance(result.body, dict):
        raise ApiError(tr("ALIGN returned an unexpected answer while following the feature service."))
    return result.body
