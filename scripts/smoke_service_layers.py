#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove the decisions around the feature-service layers (TCK-2348).

    python scripts/smoke_service_layers.py

Runs on a plain interpreter — no QGIS, no network. What is under test:

* what a layer remembers of its service, as a service layer or as its local
  original, and that another ALIGN deployment's memory reads as nothing;
* the names: the service layer is "<title> (ALIGN)", never the original's,
  and the originals are kept in "Local originals" (CA-7);
* the Services tab's model: the search (accents and case aside), the sheet
  (CA-4), the layers with their feature counts, who may edit (CA-3);
* the two reads of the API and their refusals (a session without the
  features scopes asks to sign in again; a gone service says so);
* the session feeds the signer: the token it holds or refreshes, nothing
  once signed out, and a refused refresh empties it.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import qgis_stubs  # noqa: E402

qgis_stubs.install()

from align_connector import net, services_api  # noqa: E402
from align_connector.api import ApiError  # noqa: E402
from align_connector.auth import session as session_module  # noqa: E402
from align_connector.panel import services_model  # noqa: E402
from align_connector.publish import service_memory  # noqa: E402


def check(condition, message):
    if not condition:
        raise AssertionError(message)


class _Layer(object):
    def __init__(self):
        self.props = {}

    def setCustomProperty(self, key, value):  # noqa: N802 — QGIS's name
        self.props[key] = value

    def customProperty(self, key, default=None):  # noqa: N802
        return self.props.get(key, default)

    def removeCustomProperty(self, key):  # noqa: N802
        self.props.pop(key, None)


def test_a_layer_remembers_its_service():
    ref = service_memory.ServiceRef("org-1", "svc-jardins", "0123456789abcdef", "Jardins", 2154, service_memory.SERVICE)
    layer = _Layer()
    service_memory.remember(layer, ref, service_memory.SERVICE)
    read = service_memory.read(layer)
    check(read.kind == service_memory.SERVICE and read.service_id == "svc-jardins" and read.srid == 2154, vars(read))
    check(read.can_edit, "editable unless ALIGN said otherwise")
    service_memory.set_can_edit(layer, False)
    check(not service_memory.read(layer).can_edit, "ALIGN said no: the menu offers to read")
    original = _Layer()
    service_memory.remember(original, ref, service_memory.ORIGINAL)
    check(service_memory.read(original).kind == service_memory.ORIGINAL, "the original remembers its service too (CA-6)")
    original.props[service_memory.KEY_API] = "https://staging.example"
    check(service_memory.read(original) is None, "a service of another deployment is not this one's")
    service_memory.forget(layer)
    check(service_memory.read(layer) is None and not layer.props, "forgotten entirely")
    check(service_memory.read(_Layer()) is None, "a plain layer has no service")
    # Nothing secret is written on a layer: the project saves these properties.
    check(not any("token" in key.lower() or "token" in str(value).lower() for key, value in original.props.items()), original.props)


def test_the_two_layers_never_share_a_name():
    check(service_memory.service_layer_name("parcelles") == "parcelles (ALIGN)", "CA-7")
    check(service_memory.originals_group_name() == "Local originals", "CA-7")


SERVICES = [
    {"serviceId": "svc-1", "title": "Arbres d'alignement", "description": "Patrimoine arboré", "visibility": "org", "updatedAt": "2026-10-18T09:30:00.000Z", "ownerName": "Marie Curie", "canEdit": True},
    {"serviceId": "svc-2", "title": "Électricité", "description": None, "visibility": "public", "updatedAt": "2026-10-17T08:00:00.000Z", "ownerName": None, "canEdit": False},
]
DESCRIPTION = {
    "serviceId": "svc-1",
    "title": "Arbres d'alignement",
    "layers": [
        {"layerId": "0123456789abcdef", "title": "Arbres", "geometryType": "Point", "srid": 2154, "fields": [{"name": "essence", "alias": "Essence"}, {"name": "hauteur"}], "featureCount": 12500},
        {"layerId": "fedcba9876543210", "title": "Notes", "geometryType": None, "srid": None, "fields": [], "featureCount": 1},
    ],
    "capabilities": ["add", "update"],
    "callerMay": ["add", "update"],
}


def test_the_services_tab_searches_and_describes():
    check([s["serviceId"] for s in services_model.matching(SERVICES, "ARBRE")] == ["svc-1"], "case aside")
    check([s["serviceId"] for s in services_model.matching(SERVICES, "electr")] == ["svc-2"], "accents aside")
    check([s["serviceId"] for s in services_model.matching(SERVICES, "patrimoine arbres")] == ["svc-1"], "every word, title or description")
    check(len(services_model.matching(SERVICES, "  ")) == 2, "an empty search keeps everything")
    check(services_model.access_label(SERVICES[0]) == "Editable" and services_model.access_label(SERVICES[1]) == "Read only", "CA-3")
    sheet = dict(services_model.sheet(SERVICES[0], DESCRIPTION))
    check(sheet["Visibility"] == "Organization" and sheet["Storage projection"] == "EPSG:2154", sheet)
    check(sheet["Last change"] == "2026-10-18 09:30 UTC" and sheet["Owner"] == "Marie Curie", sheet)
    check(dict(services_model.sheet(SERVICES[1], {"layers": []}))["Owner"] == "—", "an owner without a name")
    rows = services_model.layer_rows(DESCRIPTION)
    check(rows[0]["geometry"] == "Points" and rows[0]["count"] == "12 500 features" and rows[0]["fields"] == "Essence, hauteur", rows[0])
    check(rows[1]["geometry"] == "No geometry" and rows[1]["count"] == "1 feature" and rows[1]["fields"] == "No fields", rows[1])
    check(services_model.may_edit(DESCRIPTION) and not services_model.may_edit({"callerMay": []}), "who may edit is ALIGN's answer")


class _Session(object):
    def __init__(self, token="alg_at_test"):
        self._token = token

    def access_token(self):
        return self._token


def _answer(status, body):
    calls = []

    def get_json(url, bearer=None):
        calls.append((url, bearer))
        return net.HttpResult(status, body)

    services_api.net.get_json = get_json
    return calls


def _raises(call):
    try:
        call()
    except ApiError as exc:
        return exc
    raise AssertionError("expected an ApiError")


def test_the_two_reads_and_their_refusals():
    calls = _answer(200, {"services": SERVICES + [{"title": "no id"}], "truncated": True})
    listing = services_api.list_services(_Session(), "org-1")
    check([s["serviceId"] for s in listing["services"]] == ["svc-1", "svc-2"] and listing["truncated"], listing)
    check(calls[0][0].endswith("/api/v1/orgs/org-1/feature-services") and calls[0][1] == "alg_at_test", calls)
    calls = _answer(200, DESCRIPTION)
    services_api.describe_service(_Session(), "org-1", "svc-1")
    check(calls[0][0].endswith("/feature-services/svc-1?counts=true"), "the sheet asks for the counts")
    _answer(403, {"error": "INSUFFICIENT_SCOPE"})
    check(_raises(lambda: services_api.list_services(_Session(), "org-1")).needs_sign_in, "a session without features:read signs in again")
    _answer(404, {"error": "NOT_FOUND"})
    exc = _raises(lambda: services_api.describe_service(_Session(), "org-1", "svc-x"))
    check(exc.status == 404 and "gone" in exc.message, exc.message)
    _answer(200, {"layers": "nope"})
    _raises(lambda: services_api.describe_service(_Session(), "org-1", "svc-1"))
    check(_raises(lambda: services_api.list_services(_Session(token=None), "org-1")).needs_sign_in, "signed out")


class _Store(object):
    """The credential store, in memory."""

    def __init__(self, access=None, expires=0.0, refresh="rt-1"):
        self.access, self.expires, self.refresh = access, expires, refresh

    def has_session(self):
        return bool(self.refresh)

    def access_token(self):
        return self.access

    def access_expires_at(self):
        return self.expires

    def refresh_token(self):
        return self.refresh

    def update_access_token(self, access_token, access_expires_at, refresh_token=None, scopes=None):
        self.access, self.expires = access_token, access_expires_at
        self.refresh = refresh_token or self.refresh

    def clear(self):
        self.access, self.expires, self.refresh = None, 0.0, None


def test_the_session_feeds_the_signer():
    store = _Store(access="at-stored", expires=time.time() + 3000)
    session_module.token_store = store
    session = session_module.AlignSession()
    session.prime_signer()
    check(session.snapshot.current() == "at-stored", "the stored token, without a call")
    check(session.keep_signer_fresh(), "fresh enough: no refresh")

    store.expires = time.time() + 100
    session.prime_signer()
    refreshed = []

    def post_json(url, payload, bearer=None):
        refreshed.append(payload)
        return net.HttpResult(200, {"access_token": "at-new", "expires_in": 3600, "refresh_token": "rt-2"})

    session_module.net.post_json = post_json
    check(session.keep_signer_fresh() and refreshed, "about to expire: refreshed before the signer runs out")
    check(session.snapshot.current() == "at-new" and store.refresh == "rt-2", "the new token signs, the rotated one is kept")

    store.expires = time.time() + 10
    session.prime_signer()
    session_module.net.post_json = lambda url, payload, bearer=None: net.HttpResult(400, {"error": "invalid_grant"})
    check(not session.keep_signer_fresh(), "a refused refresh ends the session")
    check(session.snapshot.current() is None and not store.has_session(), "and the signer holds nothing")

    store.__init__(access="at-3", expires=time.time() + 3000)
    session.prime_signer()
    session_module.net.post_json = lambda url, payload, bearer=None: net.HttpResult(200, {})
    session.sign_out()
    check(session.snapshot.current() is None, "signed out: nothing signed")


def main():
    tests = [
        test_a_layer_remembers_its_service,
        test_the_two_layers_never_share_a_name,
        test_the_services_tab_searches_and_describes,
        test_the_two_reads_and_their_refusals,
        test_the_session_feeds_the_signer,
    ]
    for test in tests:
        test()
        print("ok ", test.__name__)
    print("{} checks passed".format(len(tests)))


if __name__ == "__main__":
    main()
