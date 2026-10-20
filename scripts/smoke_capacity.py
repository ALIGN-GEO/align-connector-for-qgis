#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove what the plugin says about an organization's remaining capacity.

    python scripts/smoke_capacity.py

Runs on a plain interpreter — no QGIS, no network. The transport is replaced
by canned answers; what is under test is the reading of ALIGN's capacity
answer (TCK-2179) and the two sentences built from it:

* the line under the organization picker: storage used and allowed, whole
  credits, never a currency, and nothing invented when ALIGN did not say;
* the refusal for space or credits, which repeats that line so the user reads
  what was missing next to what there was — and only for those two refusals;
* a 429 that says when to come back (TCK-2296, defect 69): the layer waits and
  asks again, a cancel is heard while it waits, and past the longest wait the
  refusal is the user's;
* a 403 coded RATE_LIMITED (ALIGN's ban, defect 71) is a pace refusal too,
  never a refusal of the user's role.

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
from align_connector.publish.wording import describe_capacity  # noqa: E402
from align_connector.publish.pacing import paced  # noqa: E402
from align_connector.publish.task import with_capacity  # noqa: E402
from align_connector.publish.uploader import UploadCancelled  # noqa: E402

GB = 1024 ** 3


class _Session(object):
    def __init__(self, token="alg_at_test"):
        self._token = token

    def access_token(self):
        return self._token


def _answer(status, body):
    """Replace the transport with one canned answer, and record the call."""
    calls = []

    def get_json(url, bearer=None):
        calls.append((url, bearer))
        return net.HttpResult(status, body)

    api.net.get_json = get_json
    return calls


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def test_reads_the_capacity_answer():
    calls = _answer(200, {"storage": {"usedBytes": 3 * GB, "limitBytes": 10 * GB}, "credits": {"available": 844}})
    info = api.capacity(_Session(), "org-1")
    check(
        info
        == {
            "storage": {"usedBytes": 3 * GB, "limitBytes": 10 * GB},
            "credits": {"available": 844},
            "featureServices": False,
        },
        info,
    )
    check(calls[0][0].endswith("/api/v1/orgs/org-1/capacity"), calls)
    check(calls[0][1] == "alg_at_test", "the publish token must travel as bearer")


def test_reads_whether_feature_services_are_offered():
    # TCK-2320: only a literal true offers the option; a server that does not
    # say (older than the feature) offers nothing.
    body = {"storage": {"usedBytes": 0, "limitBytes": 0}, "credits": {"available": 1}}
    _answer(200, dict(body, featureServices=True))
    check(api.capacity(_Session(), "org-1")["featureServices"] is True, "true is offered")
    _answer(200, dict(body, featureServices="yes"))
    check(api.capacity(_Session(), "org-1")["featureServices"] is False, "only a boolean true counts")


def test_never_invents_a_figure():
    _answer(200, {"storage": {"usedBytes": -5, "limitBytes": True}, "credits": {"available": "844"}})
    info = api.capacity(_Session(), "org-1")
    check(info["storage"] == {"usedBytes": None, "limitBytes": None}, info)
    check(info["credits"] == {"available": None}, info)


def test_refuses_a_malformed_answer():
    _answer(200, {"storage": "a lot"})
    try:
        api.capacity(_Session(), "org-1")
    except api.ApiError as exc:
        check("unexpected answer" in exc.message, exc.message)
    else:
        raise AssertionError("a malformed answer must be refused, not displayed")


def test_carries_the_refusal_status():
    _answer(404, {"error": "NOT_FOUND", "message": "Organization not found"})
    try:
        api.capacity(_Session(), "org-1")
    except api.ApiError as exc:
        check(exc.status == 404, exc.status)
        check(not exc.needs_sign_in, "a 404 is not a sign-in problem")
    else:
        raise AssertionError("a 404 must be refused")


def test_asks_to_sign_in_without_a_token():
    _answer(200, {})
    try:
        api.capacity(_Session(token=None), "org-1")
    except api.ApiError as exc:
        check(exc.needs_sign_in, "no token means the session is gone")
    else:
        raise AssertionError("no token must be refused before any call")


def test_describes_the_capacity():
    line = describe_capacity({"storage": {"usedBytes": 3 * GB, "limitBytes": 10 * GB}, "credits": {"available": 844}})
    check(line == "Storage 3.0 GB of 10.0 GB used · 844 credits", line)

    line = describe_capacity({"storage": {"usedBytes": 0, "limitBytes": 0}, "credits": {"available": 1}})
    check(line == "Storage 0 bytes used, no limit · 1 credit", line)

    line = describe_capacity({"storage": {"usedBytes": None, "limitBytes": None}, "credits": {"available": 12}})
    check(line == "12 credits", line)

    check(describe_capacity({}) == "", "nothing known reads as nothing, not as zeros")
    check("EUR" not in line and "€" not in line, "a credit is never shown as money")


def test_repeats_the_capacity_only_on_capacity_refusals():
    capacity = "Storage 9.8 GB of 10.0 GB used · 2 credits"
    refused = with_capacity("Not enough credits.", 402, capacity)
    check(refused == "Not enough credits. The organization currently has: {}.".format(capacity), refused)
    check(with_capacity("No space left.", 413, capacity).endswith("{}.".format(capacity)), "413 is about space")

    check(with_capacity("Your role does not allow publishing.", 403, capacity) == "Your role does not allow publishing.", "403 is not about capacity")
    check(with_capacity("Not enough credits.", 402, None) == "Not enough credits.", "no line known, nothing added")
    check(with_capacity("Could not be reached.", None, capacity) == "Could not be reached.", "no status, nothing added")


def test_the_task_title_is_said_in_the_users_language():
    from align_connector.publish import task

    real, task.tr = task.tr, lambda text: "[fr] " + text
    try:
        check(task.task_title("équipements", replacing=False) == "[fr] Publishing “équipements” to ALIGN", "QGIS's task list says it in the user's language (finding N)")
        check(task.task_title("équipements", replacing=True) == "[fr] Replacing “équipements” on ALIGN", "and a replacement too")
    finally:
        task.tr = real


def _rate_limited(retry_after="60", status=429):
    """ALIGN's 429 (or its 403 once it bans), as the transport hands it over."""
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    return net.HttpResult(status, {"error": "RATE_LIMITED", "message": "Too many requests, please try again later"}, None, headers)


def test_a_ban_is_a_pace_refusal_not_a_role_refusal():
    api.net.post_json = lambda url, payload, bearer=None: _rate_limited("30", status=403)
    refused = None
    try:
        api.open_upload_session(_Session(), "org-1", "parcels.gpkg", "application/geopackage+sqlite3", 10)
    except api.ApiError as exc:
        refused = exc
    check(refused is not None and "role" not in refused.message, refused.message if refused else None)
    check(refused.message == "ALIGN is rate-limiting this client. Wait a moment and try again.", refused.message)
    check(refused.code == "RATE_LIMITED" and refused.retry_after == 30 and refused.status == 403, vars(refused))
    api.net.post_json = lambda url, payload, bearer=None: net.HttpResult(403, {"error": "FORBIDDEN", "message": "No."}, None, {})
    try:
        api.open_upload_session(_Session(), "org-1", "parcels.gpkg", "application/geopackage+sqlite3", 10)
    except api.ApiError as exc:
        check("role" in exc.message and exc.retry_after is None, "a real role refusal stays one (edge)")

    call, seen = _calls(api.ApiError("slow down", status=403, code="RATE_LIMITED", retry_after=2), {"assetId": "a-1"})
    slept = []
    check(paced(call, cancelled=lambda: False, sleep=slept.append) == {"assetId": "a-1"} and sum(slept) == 2, (seen, slept))


def test_a_429_carries_when_to_come_back():
    api.net.post_json = lambda url, payload, bearer=None: _rate_limited("42")
    refused = None
    try:
        api.open_upload_session(_Session(), "org-1", "parcels.gpkg", "application/geopackage+sqlite3", 10)
    except api.ApiError as exc:
        refused = exc
    check(refused is not None and refused.status == 429 and refused.retry_after == 42, vars(refused) if refused else None)
    api.net.post_json = lambda url, payload, bearer=None: _rate_limited(None)
    try:
        api.open_upload_session(_Session(), "org-1", "parcels.gpkg", "application/geopackage+sqlite3", 10)
    except api.ApiError as exc:
        check(exc.retry_after is None, "no Retry-After: nothing to wait for (edge)")


def _calls(*outcomes):
    """A call that answers each outcome in turn: an exception is raised, a value returned."""
    queue = list(outcomes)
    seen = []

    def call():
        seen.append(1)
        outcome = queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return call, seen


def test_a_layer_waits_when_alignn_asks_and_asks_again():
    call, seen = _calls(api.ApiError("slow down", status=429, retry_after=3), {"assetId": "a-1"})
    slept = []
    session = paced(call, cancelled=lambda: False, sleep=slept.append)
    check(session == {"assetId": "a-1"} and len(seen) == 2, (session, seen))
    check(sum(slept) == 3, "it waited what ALIGN asked: {}".format(slept))


def test_the_wait_has_an_end_and_other_refusals_are_not_waited_for():
    call, _ = _calls(*[api.ApiError("slow down", status=429, retry_after=60)] * 20)
    try:
        paced(call, cancelled=lambda: False, sleep=lambda _s: None, max_wait=120)
        check(False, "past the longest wait, the refusal is the user's")
    except api.ApiError as exc:
        check(exc.status == 429, exc.status)
    call, seen = _calls(api.ApiError("no room", status=413))
    try:
        paced(call, cancelled=lambda: False, sleep=lambda _s: None)
    except api.ApiError:
        check(len(seen) == 1, "a refusal that is not about pace is never asked again (edge)")
    call, seen = _calls(api.ApiError("slow down", status=429))
    try:
        paced(call, cancelled=lambda: False, sleep=lambda _s: None)
    except api.ApiError:
        check(len(seen) == 1, "a 429 that does not say when: not guessed (edge)")


def test_a_cancel_is_heard_while_waiting():
    call, seen = _calls(api.ApiError("slow down", status=429, retry_after=30), {"assetId": "a-1"})
    ticks = []

    def cancelled():
        return len(ticks) >= 2

    try:
        paced(call, cancelled=cancelled, sleep=ticks.append)
        check(False, "cancelled while waiting: it stops")
    except UploadCancelled:
        check(len(seen) == 1 and len(ticks) == 2, "stopped within the wait, not after it: {} ticks".format(len(ticks)))


def main():
    tests = [
        test_a_429_carries_when_to_come_back,
        test_a_ban_is_a_pace_refusal_not_a_role_refusal,
        test_a_layer_waits_when_alignn_asks_and_asks_again,
        test_the_wait_has_an_end_and_other_refusals_are_not_waited_for,
        test_a_cancel_is_heard_while_waiting,
        test_reads_the_capacity_answer,
        test_reads_whether_feature_services_are_offered,
        test_never_invents_a_figure,
        test_refuses_a_malformed_answer,
        test_carries_the_refusal_status,
        test_asks_to_sign_in_without_a_token,
        test_describes_the_capacity,
        test_repeats_the_capacity_only_on_capacity_refusals,
        test_the_task_title_is_said_in_the_users_language,
    ]
    for test in tests:
        test()
        print("ok  {}".format(test.__name__))
    print("{} checks passed".format(len(tests)))


if __name__ == "__main__":
    main()
