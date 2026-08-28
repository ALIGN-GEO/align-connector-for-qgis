# -*- coding: utf-8 -*-
"""Reading the plugin's own metadata.

The version lives in ``metadata.txt`` because that is the file QGIS and the
plugin repository read. Anything else that needs it — the provenance recorded
against a published asset, a support question — reads it from there too, rather
than keeping a second copy that drifts by one release.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import configparser
import os

_CACHE = {}


def _general():
    if "general" not in _CACHE:
        parser = configparser.ConfigParser()
        path = os.path.join(os.path.dirname(__file__), "metadata.txt")
        try:
            parser.read(path, encoding="utf-8")
            _CACHE["general"] = dict(parser["general"]) if parser.has_section("general") else {}
        except (OSError, configparser.Error):
            # Metadata is descriptive, never load-bearing: a plugin that cannot
            # read its own version still publishes.
            _CACHE["general"] = {}
    return _CACHE["general"]


def plugin_version():
    return _general().get("version", "0.0.0")


def plugin_name():
    return _general().get("name", "ALIGN Connector")
