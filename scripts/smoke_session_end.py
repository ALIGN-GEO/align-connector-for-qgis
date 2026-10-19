#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove how a session ends, how a sign-in restarts, and what the user reads (TCK-2348, patch 0003).

    python scripts/smoke_session_end.py

Runs on a plain interpreter — no QGIS. What is under test:

* why a session ended, as ALIGN says it: withdrawn ('ended'), expired, or the
  user's own sign-out (CA-21); no network says nothing and keeps the session;
* the session asked of ALIGN on demand, and not again a moment later (CA-23);
* a sign-in left waiting is abandoned at once: the loopback port closes, the
  wait returns, a fresh attempt receives its redirect (CA-25);
* the publication notices in the user's language, by code; a code this
  plugin does not know keeps ALIGN's message; markup in a field name is
  escaped (CA-12);
* the names of a layer open for reading, and a name the user chose never
  rewritten (CA-13);
* why an organization is greyed (CA-17);
* which project files hold ALIGN service layers (CA-23).

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import pathlib
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import qgis_stubs  # noqa: E402

qgis_stubs.install()

from align_connector import net  # noqa: E402
from align_connector.auth import session as session_module  # noqa: E402
from align_connector.auth.loopback import LoopbackReceiver  # noqa: E402
from align_connector.panel import org_choice  # noqa: E402
from align_connector.publish import service_memory  # noqa: E402
from align_connector.publish.notices import notice_text, notice_texts  # noqa: E402
from align_connector.session_watch import project_mentions_services  # noqa: E402


def check(condition, message):
    if not condition:
        raise AssertionError(message)


class _Store(object):
    """The credential store, in memory."""

    def __init__(self):
        self.access, self.expires, self.refresh = "at-1", time.time() + 3000, "rt-1"

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


def _session(answer):
    store = _Store()
    session_module.token_store = store
    calls = []

    def post_json(url, payload, bearer=None):
        calls.append(payload.get("grant_type"))
        return answer() if callable(answer) else answer

    session_module.net.post_json = post_json
    session = session_module.AlignSession()
    ended = []
    session.signed_out.connect(lambda: ended.append(session.end_reason))
    return session, store, calls, ended


def test_a_session_ends_with_its_reason():
    session, store, _calls, ended = _session(net.HttpResult(400, {"error": "invalid_grant", "align_session": "ended"}))
    check(not session.verify(), "ALIGN refused the session")
    check(ended == [session_module.ENDED_BY_ALIGN] and not store.has_session(), "withdrawn: the local tokens go (CA-21)")

    session, store, _calls, ended = _session(net.HttpResult(400, {"error": "invalid_grant", "align_session": "expired"}))
    session.verify()
    check(ended == [session_module.EXPIRED], "an expiry says so")

    session, store, _calls, ended = _session(net.HttpResult(400, {"error": "invalid_grant"}))
    session.verify()
    check(ended == [session_module.ENDED_BY_ALIGN], "an older ALIGN that says nothing: ended, not expired")

    session, store, _calls, ended = _session(net.HttpResult(net.NO_RESPONSE, None, "Network error"))
    check(not session.verify(), "ALIGN could not be asked")
    check(store.has_session() and ended == [], "no network says nothing about the session: it is kept (edge)")

    session, store, _calls, ended = _session(net.HttpResult(503, {"error": "UNAVAILABLE"}))
    session.verify()
    check(store.has_session() and ended == [], "ALIGN failing says nothing either")

    session, store, _calls, ended = _session(net.HttpResult(200, {}))
    session.sign_out()
    check(ended == [session_module.ENDED_BY_USER], "the user's own sign-out")


def test_the_session_is_asked_once_in_a_while():
    fresh = net.HttpResult(200, {"access_token": "at-2", "expires_in": 3600, "refresh_token": "rt-2"})
    session, store, calls, _ended = _session(fresh)
    check(session.verify() and calls == ["refresh_token"], "asked now, whatever the token's age")
    check(session.verify_if_stale() and calls == ["refresh_token"], "and not again a moment later (CA-23)")
    session._verified_at -= 3600
    check(session.verify_if_stale() and calls == ["refresh_token", "refresh_token"], "asked again once it is old")
    store.clear()
    check(not session.verify_if_stale() and len(calls) == 2, "signed out: nothing to ask")


def test_a_waiting_sign_in_is_abandoned_at_once():
    receiver = LoopbackReceiver(expected_state="s1")
    receiver.start()
    waited = {}
    waiter = threading.Thread(target=lambda: waited.setdefault("result", receiver.wait()))
    waiter.start()
    time.sleep(0.2)
    started = time.time()
    receiver.cancel()
    waiter.join(5)
    check(not waiter.is_alive(), "the wait returned")
    check(time.time() - started < 3, "at once, not after five minutes (CA-25)")
    check(waited.get("result") == {"error": "cancelled"}, waited)
    old_port = receiver.port
    refused = False
    try:
        urllib.request.urlopen("http://127.0.0.1:{}/callback?state=s1&code=late".format(old_port), timeout=2)
    except OSError:
        refused = True
    check(refused, "the abandoned attempt's port is closed: a late redirect finds nobody")

    fresh = LoopbackReceiver(expected_state="s2")
    fresh.start()
    urllib.request.urlopen("{}?state=s2&code=alg_ac_new".format(fresh.redirect_uri), timeout=5).read()
    check(fresh.wait() == {"code": "alg_ac_new"}, "a fresh attempt receives its redirect")
    fresh.close()


def test_an_abandoned_attempt_never_answers_for_the_new_one():
    session, _store, _calls, _ended = _session(net.HttpResult(200, {}))
    signed = []
    session.signed_in.connect(lambda: signed.append(True))
    session._worker = object()  # the attempt now waiting
    session._on_finished(object(), True, "")  # a result the abandoned attempt still delivered
    check(not signed and session._worker is not None, "dropped: the waiting attempt is untouched (edge)")


def test_the_notices_are_said_by_code():
    renamed = {"code": "fieldRenamed", "params": {"from": "fid", "to": "source_fid", "reserved": "fid"}, "message": "The field \"fid\" is kept as \"source_fid\"…"}
    check(notice_text(renamed) == "The field “fid” is kept as “source_fid”: the service numbers its features itself.", notice_text(renamed))
    geom = dict(renamed, params={"from": "GEOM", "to": "source_geom", "reserved": "geom"})
    check("geometry in its own column" in notice_text(geom), notice_text(geom))
    check("2D" in notice_text({"code": "dimensionsDropped", "params": {"dimensions": "ZM"}, "message": "x"}), "dimensions")
    unknown = {"code": "somethingNew", "params": {}, "message": "A sentence from a newer ALIGN."}
    check(notice_text(unknown) == "A sentence from a newer ALIGN.", "a code this plugin does not know keeps ALIGN's message (edge)")
    hostile = {"code": "fieldBinaryDropped", "params": {"field": "<b>photo</b>"}, "message": "x"}
    check("<b>" not in notice_text(hostile) and "&lt;b&gt;photo" in notice_text(hostile), "a field name's markup is escaped")
    check(notice_texts({"notices": [renamed, unknown]})[1] == "A sentence from a newer ALIGN.", "a result's notices, in order")
    check(notice_texts({"warnings": ["From an older ALIGN."]}) == ["From an older ALIGN."], "an older ALIGN's warnings, as they were")
    for code in ("fieldBinaryDropped", "fieldRenamed", "curvesLinearized", "dimensionsDropped", "projectionUnknown", "sourceKept", "countsIncomplete"):
        golden = {"code": code, "params": {"field": "f", "from": "a", "to": "b", "reserved": "fid", "dimensions": "Z"}, "message": "MESSAGE"}
        check(notice_text(golden) != "MESSAGE", "every code ALIGN sends is said by the plugin: {}".format(code))


class _Layer(object):
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name

    def setName(self, name):  # noqa: N802 — QGIS's name
        self._name = name


def test_a_layer_open_for_reading_says_so():
    ref = service_memory.ServiceRef("org-1", "svc-1", "0123456789abcdef", "Jardins", 2154, service_memory.SERVICE)
    check(service_memory.service_layer_name("Jardins", read_only=True) == "Jardins (ALIGN, read-only)", "CA-13")
    check(service_memory.service_layer_name("Jardins") == "Jardins (ALIGN)", "in editing, as before")
    layer = _Layer("Jardins (ALIGN, read-only)")
    service_memory.rename_for_mode(layer, ref, read_only=False)
    check(layer.name() == "Jardins (ALIGN)", "the default name follows the mode")
    named = _Layer("Jardins du quartier")
    service_memory.rename_for_mode(named, ref, read_only=False)
    check(named.name() == "Jardins du quartier", "a name the user chose is never rewritten (edge)")


def test_why_an_organization_is_greyed():
    open_org = {"id": "o1", "name": "A", "connector": {"allowed": True, "featureServices": True}}
    no_services = {"id": "o2", "name": "B", "connector": {"allowed": True, "featureServices": False}}
    switched_off = {"id": "o3", "name": "C", "connector": {"allowed": False, "reason": "org-off", "featureServices": True}}
    by_role = {"id": "o4", "name": "D", "connector": {"allowed": False, "reason": "role", "featureServices": True}}
    older = {"id": "o5", "name": "E", "connector": None}
    check(org_choice.connector_refusal(open_org) is None and org_choice.services_refusal(open_org) is None, "usable")
    check(org_choice.connector_refusal(no_services) is None, "publishing files does not need feature services")
    check("plan" in org_choice.services_refusal(no_services), "the Services tab says why")
    check("Settings → Integrations" in org_choice.connector_refusal(switched_off), "switched off: who can turn it on")
    check("role" in org_choice.connector_refusal(by_role), "by role")
    check(org_choice.services_refusal(older) is None, "an older ALIGN says nothing: nothing greyed (edge)")


def test_which_projects_hold_service_layers():
    folder = pathlib.Path(tempfile.mkdtemp())
    qgs = folder / "with.qgs"
    qgs.write_text('<qgis><datasource>url=\'https://api.example/api/v1/orgs/o/feature-services/s/ogc\'</datasource></qgis>', encoding="utf-8")
    plain = folder / "plain.qgs"
    plain.write_text("<qgis><datasource>./roads.gpkg</datasource></qgis>", encoding="utf-8")
    qgz = folder / "with.qgz"
    with zipfile.ZipFile(qgz, "w") as archive:
        archive.write(qgs, "with.qgs")
    check(project_mentions_services(str(qgs)) and project_mentions_services(str(qgz)), ".qgs and .qgz")
    check(not project_mentions_services(str(plain)), "a project without ALIGN service layers is not asked about")
    check(not project_mentions_services(str(folder / "missing.qgs")) and not project_mentions_services(""), "nothing to read says no (edge)")


def main():
    tests = [
        test_a_session_ends_with_its_reason,
        test_the_session_is_asked_once_in_a_while,
        test_a_waiting_sign_in_is_abandoned_at_once,
        test_an_abandoned_attempt_never_answers_for_the_new_one,
        test_the_notices_are_said_by_code,
        test_a_layer_open_for_reading_says_so,
        test_why_an_organization_is_greyed,
        test_which_projects_hold_service_layers,
    ]
    for test in tests:
        test()
        print("ok ", test.__name__)
    print("{} checks passed".format(len(tests)))


if __name__ == "__main__":
    main()
