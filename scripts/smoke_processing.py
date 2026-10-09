#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The Processing provider, on a real QGIS (TCK-2181).

Run it with the Python of a QGIS install (or inside the ``qgis/qgis``
image). No account, no network: it proves what the toolbox will see
(provider, algorithm, parameters, outputs), that the algorithm refuses to
start signed out, and how an organization name or id resolves against a
fake session.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from qgis.core import (  # noqa: E402
    QgsApplication,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsVectorLayer,
)

failures = []


def check(label, condition, detail=""):
    print("{}  {}{}".format("ok " if condition else "BAD", label, "  [{}]".format(detail) if detail else ""))
    if not condition:
        failures.append(label)


app = QgsApplication([], False)
app.initQgis()

from align_connector import api  # noqa: E402
from align_connector.processing import publish_layer  # noqa: E402
from align_connector.processing.provider import PROVIDER_ID, AlignProcessingProvider  # noqa: E402


class FakeSession:
    def __init__(self, signed_in):
        self._signed_in = signed_in

    def is_signed_in(self):
        return self._signed_in


# -- what the toolbox sees -----------------------------------------------------
provider = AlignProcessingProvider(FakeSession(True), os.path.join(ROOT, "align_connector"))
registry = QgsApplication.processingRegistry()
check("the provider registers", registry.addProvider(provider) and registry.providerById(PROVIDER_ID) is provider)
algorithm = registry.algorithmById("align:publishlayer")
check("align:publishlayer is in the registry", algorithm is not None)
names = [p.name() for p in algorithm.parameterDefinitions()]
check("the parameters are the panel's choices", names == ["INPUT", "ORGANIZATION", "TITLE", "DESCRIPTION", "VISIBILITY", "SELECTED_ONLY", "REPLACE"], names)
outputs = [o.name() for o in algorithm.outputDefinitions()]
check("the outputs name the item", outputs == ["ASSET_ID", "ALIGN_URL", "SERVABLE_ASSET_ID"], outputs)
check("the provider carries the ALIGN icon", not provider.icon().isNull())
check("the help says to sign in first and names the panel for services", "Sign in" in algorithm.shortHelpString() and "reference" in algorithm.shortHelpString())

# -- signed out: refused before anything else ---------------------------------
layer = QgsVectorLayer("Point?crs=EPSG:4326&field=name:string", "points", "memory")
signed_out = publish_layer.PublishLayerAlgorithm(FakeSession(False))
signed_out.initAlgorithm()
context = QgsProcessingContext()
feedback = QgsProcessingFeedback()
try:
    signed_out.prepareAlgorithm({"INPUT": layer, "ORGANIZATION": "x", "VISIBILITY": 0, "SELECTED_ONLY": False}, context, feedback)
    check("signed out, prepare refuses", False, "no exception")
except QgsProcessingException as exc:
    check("signed out, prepare refuses with the sign-in sentence", "Sign in" in str(exc), str(exc))

# -- the organization parameter -----------------------------------------------
orgs = [
    {"id": "org-a", "name": "Town GIS", "role": "owner"},
    {"id": "org-b", "name": "Twin", "role": "member"},
    {"id": "org-c", "name": "twin", "role": "member"},
]
api.list_organizations = lambda session: orgs
session = FakeSession(True)
check("an id resolves", publish_layer.resolve_organization(session, "org-b")["name"] == "Twin")
check("a name resolves, case-insensitively, when unique", publish_layer.resolve_organization(session, "town gis")["id"] == "org-a")
for wanted, expect in (("twin", "Several organizations"), ("nowhere", "No organization"), ("", "Give the organization")):
    try:
        publish_layer.resolve_organization(session, wanted)
        check("'{}' is refused".format(wanted), False, "no exception")
    except QgsProcessingException as exc:
        check("'{}' is refused with the list of organizations".format(wanted or "(empty)"), expect in str(exc) and ("org-a" in str(exc) or not wanted or wanted == "twin"), str(exc)[:90])

check("the provider unregisters", registry.removeProvider(PROVIDER_ID) and registry.providerById(PROVIDER_ID) is None)

print("")
print("{} failure(s)".format(len(failures)))
sys.exit(1 if failures else 0)
