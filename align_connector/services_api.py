# -*- coding: utf-8 -*-
"""Reading the organization's feature services (TCK-2348, CA-3 and CA-4).

Two reads, both with the ``features:read`` the single consent grants: the
services the signed-in user may read, each with whether they may edit it,
decided by ALIGN; and one service's layers, with their feature counts. Nothing
here decides a right: the plugin shows what ALIGN answers.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from . import config, net
from .api import ApiError
from .i18n import tr


def _refused(result, fallback):
    """A refusal, in the words of someone browsing the services."""
    body = result.body if isinstance(result.body, dict) else {}
    code = body.get("error") or body.get("code")
    if result.status == 401:
        raise ApiError(tr("Your ALIGN session has expired. Sign in again."), needs_sign_in=True, status=401)
    if result.status == 403 and code == "INSUFFICIENT_SCOPE":
        raise ApiError(
            tr("Sign in again to let the plugin read your feature services."), needs_sign_in=True, status=403, code=code
        )
    if result.status == 403:
        raise ApiError(tr("Feature services are not part of this organization's plan."), status=403, code=code)
    if result.status == 404:
        raise ApiError(tr("This feature service is gone, or you can no longer read it."), status=404, code=code)
    if result.status == 429:
        raise ApiError(tr("ALIGN is rate-limiting this client. Wait a moment and try again."), status=429)
    if result.status == net.NO_RESPONSE:
        raise ApiError(tr("ALIGN could not be reached. Check your connection and try again."))
    raise ApiError(result.message(fallback), status=result.status, code=code)


def _token(session):
    token = session.access_token()
    if not token:
        raise ApiError(tr("You are not signed in to ALIGN."), needs_sign_in=True)
    return token


def list_services(session, org_id):
    """The organization's feature services the user may read.

    :returns: ``{"services": [{"serviceId", "title", "description", "visibility",
        "updatedAt", "ownerName", "canEdit"}], "truncated"}``
    :raises ApiError: when the read fails
    """
    result = net.get_json(config.feature_services_endpoint(org_id), bearer=_token(session))
    if not result.ok:
        _refused(result, tr("Could not read the feature services"))
    body = result.body if isinstance(result.body, dict) else {}
    services = body.get("services")
    if not isinstance(services, list):
        raise ApiError(tr("ALIGN returned an unexpected answer while listing the feature services."))
    return {
        "services": [service for service in services if isinstance(service, dict) and service.get("serviceId")],
        "truncated": body.get("truncated") is True,
    }


def describe_service(session, org_id, service_id, counts=True):
    """One service: its layers (geometry, projection, fields, input rules,
    feature count), and what the user may do with it (``callerMay``).

    :param counts: count each layer's features; the Services tab shows them,
        the opening of a layer does not need them
    :raises ApiError: when the read fails
    """
    result = net.get_json(config.feature_service_endpoint(org_id, service_id, counts), bearer=_token(session))
    if not result.ok:
        _refused(result, tr("Could not read the feature service"))
    body = result.body if isinstance(result.body, dict) else None
    if not body or not isinstance(body.get("layers"), list):
        raise ApiError(tr("ALIGN returned an unexpected answer while reading the feature service."))
    return body
