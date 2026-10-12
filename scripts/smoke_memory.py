#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""What a layer remembers of its publication, and what a replacement says.

Runs with any Python — no QGIS: the memory lives in a layer's custom
properties, which a dict can stand in for; the plan and the outcome are
pure. Proves (TCK-2218, ADR-0058 V1-e):

* a layer remembers the item it became, and forgets it on request;
* a memory from another ALIGN deployment reads as "never published";
* a batch replaces the remembered item only in its own organization, and
  only unless the publisher asked for a new item;
* the Publish button counts what it replaces;
* a finished replacement names the schema changes, and a replacement whose
  item was gone says it was published anew.

    python scripts/smoke_memory.py

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import pathlib
import sys
import types

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import qgis_stubs  # noqa: E402

qgis_stubs.install()

from align_connector import config  # noqa: E402
from align_connector.panel import batch, outcome  # noqa: E402
from align_connector.publish import memory  # noqa: E402


def check(condition, detail):
    if not condition:
        raise AssertionError(detail)


class Layer(object):
    """A layer reduced to its custom properties."""

    def __init__(self, layer_id="v1", name="Parcels"):
        self._id = layer_id
        self._name = name
        self.props = {}

    def id(self):
        return self._id

    def name(self):
        return self._name

    def customProperty(self, key, default=None):  # noqa: N802 — QGIS' name
        return self.props.get(key, default)

    def setCustomProperty(self, key, value):  # noqa: N802 — QGIS' name
        self.props[key] = value

    def removeCustomProperty(self, key):  # noqa: N802 — QGIS' name
        self.props.pop(key, None)


def test_a_layer_remembers_the_item_it_became_and_forgets_it():
    layer = Layer()
    check(memory.read(layer) is None, "never published reads as None")

    memory.remember(layer, "asset-1", "org-1", org_name="Town GIS", title="Parcels 2026", published_at="2026-10-09T18:00:00Z")
    link = memory.read(layer)
    check(link is not None and link.asset_id == "asset-1" and link.org_id == "org-1", "the link reads back")
    check(link.org_name == "Town GIS" and link.title == "Parcels 2026", "name and title read back")
    check(link.api_url == config.api_url(), "the deployment is recorded")
    check(memory.describe(link) == "Already on ALIGN: published in Town GIS on 2026-10-09.", memory.describe(link))

    memory.forget(layer)
    check(memory.read(layer) is None and layer.props == {}, "forgotten entirely")


def test_a_memory_from_another_deployment_reads_as_never_published():
    layer = Layer()
    memory.remember(layer, "asset-1", "org-1")
    layer.props[memory.KEY_API] = "https://staging.example"
    check(memory.read(layer) is None, "another server's item is not this server's")

    half = Layer()
    half.props[memory.KEY_ASSET] = "asset-1"
    check(memory.read(half) is None, "an asset without an organization is no link")

    bare = types.SimpleNamespace()
    check(memory.read(bare) is None, "an object without custom properties is no link")
    memory.remember(bare, "a", "o")  # must not raise
    memory.forget(bare)


def test_a_batch_replaces_only_in_its_own_organization_unless_asked_otherwise():
    link = memory.Link("asset-1", "org-1", org_name="Town GIS")
    rows = [
        batch.Row("v1", "Parcels", "vector", link=link),
        batch.Row("v2", "Roads", "vector"),
        batch.Row("v3", "Rivers", "vector", link=memory.Link("asset-9", "org-2")),
    ]
    plans = batch.plan(rows, checked_ids=["v1", "v2", "v3"], org_id="org-1")
    check([p.replace.asset_id if p.replace else None for p in plans] == ["asset-1", None, None], "only the item of this organization")
    check(batch.describe_plan(plans) == "Publish 3 layers (1 replaced)", batch.describe_plan(plans))

    as_new = batch.plan(rows, checked_ids=["v1"], org_id="org-1", publish_as_new=["v1"])
    check(as_new[0].replace is None, "the publisher asked for a new item")
    check(batch.describe_plan(as_new) == "Publish 1 layer", batch.describe_plan(as_new))

    one = batch.plan(rows, checked_ids=["v1"], org_id="org-1")
    check(batch.describe_plan(one) == "Replace 1 layer", batch.describe_plan(one))
    both = batch.plan(rows + [batch.Row("v4", "Parks", "vector", link=memory.Link("asset-2", "org-1"))], checked_ids=["v1", "v4"], org_id="org-1")
    check(batch.describe_plan(both) == "Replace 2 layers", batch.describe_plan(both))

    elsewhere = batch.plan(rows, checked_ids=["v1"], org_id="org-2")
    check(elsewhere[0].replace is None, "a batch to another organization publishes anew")
    check(batch.replaceable(rows[0], None) is None, "no organization, nothing to replace")


def _task(**attrs):
    base = dict(
        asset_name="Parcels", org_name="Town GIS", asset_id="asset-1", derivation_error=None,
        flattened_fields=[], servable_asset_id=None, gis_format="flatgeobuf", locked_fields=[],
        was_cancelled=False, error_message=None, replaced=False, replaced_missing=False, schema_changes=None,
    )
    base.update(attrs)
    return types.SimpleNamespace(**base)


def test_a_replacement_says_what_changed_and_a_gone_item_says_it_was_published_anew():
    same = outcome.outcome_of(_task(replaced=True, schema_changes={"added": [], "removed": [], "retyped": []}), True)
    check(same.level == outcome.SUCCESS and same.asset_id == "asset-1", "a replacement opens the same item")
    check(
        same.text == "“Parcels” was replaced in Town GIS on ALIGN; the web maps that show it now show this version. "
        "The attribute fields are unchanged. The previous version is kept on ALIGN.",
        same.text,
    )

    changed = outcome.outcome_of(
        _task(replaced=True, schema_changes={"added": ["region"], "removed": ["area"], "retyped": ["pop: Int to Long"]}), True
    )
    check("Attribute fields changed: added region; removed area; retyped pop: Int to Long." in changed.text, changed.text)
    check("A map that reads a removed field will need attention." in changed.text, changed.text)

    grown = outcome.outcome_of(_task(replaced=True, schema_changes={"added": ["region"], "removed": [], "retyped": []}), True)
    check("Attribute fields changed: added region." in grown.text, grown.text)
    check("removed field" not in grown.text, "no warning when nothing was removed: " + grown.text)

    uncompared = outcome.outcome_of(_task(replaced=True, schema_changes=None), True)
    check("The attribute fields were not compared for this format." in uncompared.text, uncompared.text)

    gone = outcome.outcome_of(_task(replaced_missing=True), True)
    check(
        gone.text == "The item “Parcels” was published as no longer existed on ALIGN, so it was published as a new item in Town GIS.",
        gone.text,
    )

    refused = outcome.outcome_of(_task(error_message="Only the item's creator or an organization admin can replace it."), False)
    check(refused.level == outcome.WARNING and refused.text.startswith("Only the item"), refused.text)


def main():
    tests = [
        test_a_layer_remembers_the_item_it_became_and_forgets_it,
        test_a_memory_from_another_deployment_reads_as_never_published,
        test_a_batch_replaces_only_in_its_own_organization_unless_asked_otherwise,
        test_a_replacement_says_what_changed_and_a_gone_item_says_it_was_published_anew,
    ]
    for test in tests:
        test()
    print("OK — {} checks on the publication memory and the replacement".format(len(tests)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
