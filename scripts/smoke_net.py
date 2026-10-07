#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove how a network answer is read, whichever Qt class fetched it.

    python scripts/smoke_net.py

Runs on a plain interpreter — no QGIS, no network. The transport is not under
test here (it cannot be, without Qt); the reading of what it returns is, and
it is shared by the JSON calls to ALIGN and by the chunks of a resumable
upload (TCK-2195):

* a ``308`` is a status with a ``Range`` header, never an error, never a
  redirect — it is how the store says "keep going, I hold this much";
* a ``4xx``/``5xx`` keeps its status and its JSON body, even though Qt flags
  it as an error: the status is the verdict;
* only a transport failure with no status at all is reported as an error,
  with the sentence Qt gave;
* an object store's bare ``200`` with no body is a success.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import pathlib
import sys
import types

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _install_qt_stubs():
    """Stand in for the Qt and QGIS names ``net`` imports at load time."""
    core = types.ModuleType("qgis.core")
    for name in ("QgsBlockingNetworkRequest", "QgsNetworkAccessManager"):
        setattr(core, name, type(name, (object,), {}))

    qtcore = types.ModuleType("qgis.PyQt.QtCore")
    for name in ("QByteArray", "QEventLoop", "QUrl"):
        setattr(qtcore, name, type(name, (object,), {}))

    network = types.ModuleType("qgis.PyQt.QtNetwork")
    for name in ("QNetworkReply", "QNetworkRequest"):
        setattr(network, name, type(name, (object,), {}))

    qgis = types.ModuleType("qgis")
    pyqt = types.ModuleType("qgis.PyQt")
    qgis.core = core
    qgis.PyQt = pyqt
    pyqt.QtCore = qtcore
    pyqt.QtNetwork = network

    sys.modules.update(
        {
            "qgis": qgis,
            "qgis.core": core,
            "qgis.PyQt": pyqt,
            "qgis.PyQt.QtCore": qtcore,
            "qgis.PyQt.QtNetwork": network,
        }
    )


_install_qt_stubs()

from align_connector import net  # noqa: E402


class _Reply(object):
    """The two header accessors both Qt's reply and QGIS' content expose."""

    def __init__(self, headers):
        self._headers = {k.encode("latin-1"): v.encode("latin-1") for k, v in headers.items()}

    def rawHeaderList(self):  # noqa: N802 — Qt naming
        return list(self._headers)

    def rawHeader(self, name):  # noqa: N802 — Qt naming
        return self._headers[name]


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def test_a_308_is_progress_not_an_error():
    headers = net._headers_of(_Reply({"Range": "bytes=0-8388607", "X-GUploader-UploadID": "abc"}))
    result = net._reduce(308, headers, b"", False, "")
    check(result.status == 308, result.status)
    check(result.error is None, "a 308 is not an error")
    check(not result.ok, "a 308 is not a success either: the object is not complete")
    check(result.headers["range"] == "bytes=0-8388607", result.headers)
    check(result.body == {}, "no body reads as an empty document")


def test_headers_are_lower_cased_whatever_the_server_sent():
    headers = net._headers_of(_Reply({"RANGE": "bytes=0-99", "Content-Length": "0"}))
    check(headers == {"range": "bytes=0-99", "content-length": "0"}, headers)


def test_a_refusal_keeps_its_status_and_its_sentence():
    body = b'{"error":"NOT_FOUND","message":"Upload session expired"}'
    # Qt flags any 4xx as an error on the reply; the status is still the verdict.
    result = net._reduce(404, {}, body, True, "Error transferring - server replied: Not Found")
    check(result.status == 404, result.status)
    check(result.error is None, "the status is the verdict, not the transport sentence")
    check(result.code() == "NOT_FOUND", result.code())
    check(result.message() == "Upload session expired", result.message())


def test_only_a_transport_failure_is_an_error():
    result = net._reduce(net.NO_RESPONSE, {}, b"", True, "Connection refused")
    check(result.status == net.NO_RESPONSE, result.status)
    check(result.error == "Connection refused", result.error)
    check(result.message("fallback") == "Connection refused", result.message("fallback"))

    silent = net._reduce(net.NO_RESPONSE, {}, b"", True, "")
    check(silent.error == "Network error", "a failure without a sentence still has one")


def test_a_bare_200_is_a_success():
    result = net._reduce(200, {"etag": '"x"'}, b"", False, "")
    check(result.ok, "an object store answers 200 with no body")
    check(result.body == {}, result.body)

    noisy = net._reduce(200, {}, b"<html>not json</html>", False, "")
    check(noisy.ok and noisy.body == {}, "a non-JSON body is not a failure, it is just not a document")


def main():
    tests = [
        test_a_308_is_progress_not_an_error,
        test_headers_are_lower_cased_whatever_the_server_sent,
        test_a_refusal_keeps_its_status_and_its_sentence,
        test_only_a_transport_failure_is_an_error,
        test_a_bare_200_is_a_success,
    ]
    for test in tests:
        test()
        print("ok  {}".format(test.__name__))
    print("{} checks passed".format(len(tests)))


if __name__ == "__main__":
    main()
