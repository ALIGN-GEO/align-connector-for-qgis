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
  what was missing next to what there was — and only for those two refusals.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import pathlib
import sys
import types

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _install_qgis_stubs():
    """Stand in for the QGIS and Qt classes the modules import at load time.

    No widget is ever built here: the dialog module is imported for its
    sentence, the task module for its refusal. Everything else is a name.
    """
    core = types.ModuleType("qgis.core")
    for name in (
        "Qgis",
        "QgsBlockingNetworkRequest",
        "QgsCoordinateReferenceSystem",
        "QgsCoordinateTransform",
        "QgsCoordinateTransformContext",
        "QgsFeature",
        "QgsFields",
        "QgsField",
        "QgsMessageLog",
        "QgsNetworkAccessManager",
        "QgsProject",
        "QgsRasterLayer",
        "QgsRasterPipe",
        "QgsTask",
        "QgsVectorLayer",
        "QgsWkbTypes",
    ):
        setattr(core, name, type(name, (object,), {}))

    class QgsVectorFileWriter(object):
        NoError = 0

    class QgsRasterFileWriter(object):
        NoError = 0

        def __init__(self, path):
            self.path = path

    core.QgsVectorFileWriter = QgsVectorFileWriter
    core.QgsRasterFileWriter = QgsRasterFileWriter

    qtcore = types.ModuleType("qgis.PyQt.QtCore")
    qtcore.QVariant = type("QVariant", (object,), {"Map": 8, "List": 9, "StringList": 11, "Hash": 28})
    qtcore.Qt = type("Qt", (object,), {"PlainText": 0, "WaitCursor": 3})
    qtcore.QSettings = type("QSettings", (object,), {"value": lambda self, key, default=None: default})
    for name in ("QByteArray", "QEventLoop", "QUrl"):
        setattr(qtcore, name, type(name, (object,), {}))

    widgets = types.ModuleType("qgis.PyQt.QtWidgets")
    for name in (
        "QApplication",
        "QCheckBox",
        "QComboBox",
        "QDialog",
        "QDialogButtonBox",
        "QFormLayout",
        "QLabel",
        "QLineEdit",
        "QPlainTextEdit",
        "QVBoxLayout",
    ):
        setattr(widgets, name, type(name, (object,), {}))

    network = types.ModuleType("qgis.PyQt.QtNetwork")
    for name in ("QNetworkAccessManager", "QNetworkReply", "QNetworkRequest"):
        setattr(network, name, type(name, (object,), {}))

    qgis = types.ModuleType("qgis")
    pyqt = types.ModuleType("qgis.PyQt")
    qgis.core = core
    qgis.PyQt = pyqt
    pyqt.QtCore = qtcore
    pyqt.QtWidgets = widgets
    pyqt.QtNetwork = network

    sys.modules.update(
        {
            "qgis": qgis,
            "qgis.core": core,
            "qgis.PyQt": pyqt,
            "qgis.PyQt.QtCore": qtcore,
            "qgis.PyQt.QtWidgets": widgets,
            "qgis.PyQt.QtNetwork": network,
        }
    )


_install_qgis_stubs()

from align_connector import api, net  # noqa: E402
from align_connector.publish.dialog import describe_capacity  # noqa: E402
from align_connector.publish.task import with_capacity  # noqa: E402

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
    check(info == {"storage": {"usedBytes": 3 * GB, "limitBytes": 10 * GB}, "credits": {"available": 844}}, info)
    check(calls[0][0].endswith("/api/v1/orgs/org-1/capacity"), calls)
    check(calls[0][1] == "alg_at_test", "the publish token must travel as bearer")


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


def main():
    tests = [
        test_reads_the_capacity_answer,
        test_never_invents_a_figure,
        test_refuses_a_malformed_answer,
        test_carries_the_refusal_status,
        test_asks_to_sign_in_without_a_token,
        test_describes_the_capacity,
        test_repeats_the_capacity_only_on_capacity_refusals,
    ]
    for test in tests:
        test()
        print("ok  {}".format(test.__name__))
    print("{} checks passed".format(len(tests)))


if __name__ == "__main__":
    main()
