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


class ApiError(Exception):
    """A refusal, already phrased for a human.

    :param message: what to show
    :param needs_sign_in: the session is gone; the caller should offer to reconnect
    """

    def __init__(self, message, needs_sign_in=False):
        super().__init__(message)
        self.message = message
        self.needs_sign_in = needs_sign_in


# What each refusal means to someone publishing a layer. The API's own message
# is used when it is specific enough; these cover the cases where it is not.
_REFUSALS = {
    401: ("Your ALIGN session has expired. Sign in again to publish.", True),
    403: ("Your role in this organization does not allow publishing.", False),
    404: ("That organization could not be found, or you are no longer a member of it.", False),
    413: ("There is not enough space left in this organization's Datastore.", False),
    415: ("ALIGN does not accept this file type.", False),
    429: ("ALIGN is rate-limiting this client. Wait a moment and try again.", False),
}


def _raise_for(result, fallback):
    """Turn a failed response into an :class:`ApiError` a user can act on."""
    known = _REFUSALS.get(result.status)
    if known:
        message, needs_sign_in = known
        # A specific server message beats our generic one — except for 401,
        # where "invalid token" tells the user nothing they can do.
        if result.status != 401:
            message = result.message(message)
        raise ApiError(message, needs_sign_in=needs_sign_in)
    if result.status == net.NO_RESPONSE:
        raise ApiError("ALIGN could not be reached. Check your connection and try again.")
    raise ApiError(result.message(fallback))


def list_organizations(session):
    """The organizations the signed-in user belongs to.

    :returns: list of ``{"id", "name", "role"}``, in the order ALIGN returns them
    :raises ApiError: when the call fails
    """
    token = session.access_token()
    if not token:
        raise ApiError("You are not signed in to ALIGN.", needs_sign_in=True)

    result = net.get_json(config.orgs_endpoint(), bearer=token)
    if not result.ok:
        _raise_for(result, "Could not read your organizations")

    items = result.body.get("items") if isinstance(result.body, dict) else None
    if not isinstance(items, list):
        raise ApiError("ALIGN returned an unexpected answer while listing organizations.")

    return [
        {"id": item.get("id"), "name": item.get("name"), "role": item.get("role")}
        for item in items
        if isinstance(item, dict) and item.get("id")
    ]


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
        raise ApiError("You are not signed in to ALIGN.", needs_sign_in=True)

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
            raise ApiError(result.message("This file is too large for ALIGN to prepare for display."))
        _raise_for(result, "ALIGN refused the upload")

    body = result.body
    for key in ("assetId", "sessionUrl", "chunkSizeBytes"):
        if not body.get(key):
            raise ApiError("ALIGN returned an incomplete upload session.")
    return body


def finalize(session, org_id, asset_id, filename, name, provenance=None, description=None, visibility=None):
    """Catalogue the asset once its bytes have landed.

    The declared size is not re-sent: ALIGN measures what actually arrived, and
    that measurement is the one that counts against the quota.
    """
    token = session.access_token()
    if not token:
        raise ApiError("Your ALIGN session expired while the file was uploading.", needs_sign_in=True)

    payload = {"filename": filename, "name": name}
    if provenance:
        payload["provenance"] = provenance
    # Sent in the same call that catalogues the bytes: a second call could fail
    # and leave the asset with a visibility nobody chose.
    if description:
        payload["description"] = description
    if visibility:
        payload["visibility"] = visibility

    result = net.post_json(config.finalize_endpoint(org_id, asset_id), payload, bearer=token)
    if not result.ok:
        _raise_for(result, "ALIGN could not catalogue the uploaded file")
    return result.body
