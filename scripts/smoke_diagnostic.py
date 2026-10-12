# -*- coding: utf-8 -*-
"""Smoke: the diagnostic a user pastes in a bug report (TCK-2219).

Runs without QGIS (``python scripts/smoke_diagnostic.py``): the composition
takes plain values. What is proven here:

* the text carries the versions, the addresses and whether they are the
  defaults, the session state, the organizations by name and role, the
  last publications with ALIGN's status and code, and the refused layers;
* it is in English whatever the plugin's language (it is for the support);
* a token, a bearer header or an e-mail address that reaches the composer
  through a message is masked, and the account identity is never read;
* the history is bounded to ``MAX_HISTORY`` entries, newest first.

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

from align_connector import diagnostic  # noqa: E402

FAILURES = []


def check(condition, detail):
    print(("ok   " if condition else "FAIL ") + str(detail))
    if not condition:
        FAILURES.append(detail)


ENV = {
    "plugin": "ALIGN Connector 0.5.0",
    "language": "fr",
    "qgis": "3.44.10-Solothurn",
    "qt": "5.15.13",
    "pyqt": "5.15.10",
    "python": "3.12.6",
    "system": "Windows-11-10.0.26200-SP0",
    "app_url": "https://studio-gis-builder-v001.web.app",
    "app_custom": True,
    "api_url": "https://api.aligngeo.com",
    "api_custom": False,
}


def test_the_text_says_what_the_support_needs_in_english():
    history = [
        {"title": "Parcels", "org": "Town GIS", "level": "warning",
         "text": "This organization has not enabled the QGIS plugin for its members.", "status": 403,
         "code": "QGIS_CONNECTOR_NOT_ALLOWED"},
        {"title": "Roads", "org": "Town GIS", "level": "success", "text": "“Roads” is now in Town GIS on ALIGN.",
         "status": None, "code": None},
    ]
    text = diagnostic.compose(
        ENV, True, [{"name": "Town GIS", "role": "owner"}, {"name": "Région", "role": "member"}],
        history, [("WMS basemap", "A service layer: register it as a reference.")], now=1791800000,
    )
    check(text.startswith("ALIGN Connector diagnostic\nGenerated 2026-10-12 "), text.splitlines()[:2])
    check("No token, no e-mail address" in text, "the header says what the text holds")
    check("Plugin: ALIGN Connector 0.5.0 (language: fr)" in text, "plugin and language")
    check("QGIS: 3.44.10-Solothurn | Qt 5.15.13 | PyQt 5.15.10 | Python 3.12.6" in text, "versions on one line")
    check("Application: https://studio-gis-builder-v001.web.app (custom)" in text, "a custom address says so")
    check("API: https://api.aligngeo.com (default)" in text, "a default address says so")
    check("Session: signed in" in text, "session state")
    check("Organizations: Town GIS (owner), Région (member)" in text, "organizations by name and role, no id")
    check("- Parcels -> Town GIS: WARNING: This organization has not enabled the QGIS plugin for its members. "
          "[403 QGIS_CONNECTOR_NOT_ALLOWED]" in text, "a refusal carries ALIGN's status and code")
    check("- Roads -> Town GIS: SUCCESS: “Roads” is now in Town GIS on ALIGN.\n" in text, "a success has no code")
    check("- WMS basemap: A service layer: register it as a reference." in text, "refused layers with their reason")
    check("Champs" not in text and "connecté" not in text, "English whatever the plugin's language")


def test_empty_states_are_said_not_blank():
    text = diagnostic.compose(ENV, False, None, [], [], now=0)
    check("Session: signed out" in text, "signed out")
    check("Organizations: not loaded (the panel was not opened)" in text, "never loaded")
    check("- none in this session" in text, "no publication yet")
    check("Layers the panel refuses:\n- none" in text, "no refused layer")
    none_text = diagnostic.compose(ENV, True, [], [], [], now=0)
    check("Organizations: none" in none_text, "loaded, but none")


def test_a_secret_that_slips_in_through_a_message_is_masked():
    leaky = [
        {"title": "Parcels", "org": "Town GIS", "level": "warning",
         "text": "ALIGN answered 401 for Bearer alg_at_gUH88abc.def and user someone@example.org",
         "status": 401, "code": None},
    ]
    text = diagnostic.compose(ENV, True, [{"name": "alg_rt_notaname", "role": "owner"}], leaky,
                              [("layer", "contact jane.doe+gis@geonova.be")], now=0)
    for forbidden in ("alg_at_", "alg_rt_", "Bearer alg", "@example.org", "@geonova.be"):
        check(forbidden not in text, "masked: " + forbidden)
    check(text.count(diagnostic.REDACTED) >= 4, "each secret shape is replaced, not dropped silently")
    check("ALIGN answered 401 for [redacted] and user [redacted]" in text, "the sentence around it survives")


def test_the_history_is_bounded_newest_first():
    history = [{"title": "L{}".format(i), "org": "O", "level": "success", "text": "ok", "status": None, "code": None}
               for i in range(diagnostic.MAX_HISTORY + 5)]
    text = diagnostic.compose(ENV, True, [], history, [], now=0)
    lines = [line for line in text.splitlines() if line.startswith("- L")]
    check(len(lines) == diagnostic.MAX_HISTORY, "{} lines".format(len(lines)))
    check(lines[0].startswith("- L0 ") and lines[-1].startswith("- L{} ".format(diagnostic.MAX_HISTORY - 1)), lines[0] + " … " + lines[-1])


def test_the_environment_degrades_to_unknown_outside_qgis():
    env = diagnostic.environment()
    check(env["plugin"].startswith("ALIGN Connector"), env["plugin"])
    check(env["api_url"] == "https://api.aligngeo.com" and env["api_custom"] is False, (env["api_url"], env["api_custom"]))
    check(all(isinstance(env[k], str) and env[k] for k in ("qgis", "qt", "pyqt", "python", "system")), "every version is a string")
    text = diagnostic.compose(env, False, None, [], [])
    check("ALIGN Connector diagnostic" in text, "composes on the real environment")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print("--", name)
            fn()
    print()
    if FAILURES:
        print("{} failure(s)".format(len(FAILURES)))
        sys.exit(1)
    print("OK — the diagnostic says what the support needs, and never a secret")
