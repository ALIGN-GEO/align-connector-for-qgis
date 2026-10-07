#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove what the plugin reads from a layer's metadata, and what it says.

    python scripts/smoke_isometa.py

Runs on a plain interpreter — no QGIS. The metadata record is a stand-in
answering ``QgsLayerMetadata``'s method names; what is under test is the
subset ALIGN's item sheet shows (TCK-2202):

* abstract, keywords across vocabularies (deduplicated, 32 at most), the
  INSPIRE theme from an INSPIRE vocabulary, licence (else rights), access
  constraints, one contact with organisation and email, lineage;
* nothing the sheet does not show — addresses, phones, links — ever travels;
* the sentence a publisher reads, and the fields the organization kept as
  its own after the publish.

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

from align_connector.publish import isometa  # noqa: E402
from align_connector.publish.task import locked_fields_of  # noqa: E402


class Record(object):
    """A QgsLayerMetadata stand-in: methods for the record's fields."""

    def __init__(self, **fields):
        self._fields = fields

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._fields:
            value = self._fields[name]
            return lambda: value
        raise AttributeError(name)


def layer(record, abstract=""):
    return Record(metadata=record, abstract=abstract, name="layer")


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def test_the_sheet_fields_travel_and_nothing_else():
    record = Record(
        title="Parcels 2026",
        abstract="  Cadastral parcels of the town.  ",
        keywords={"gmd:topicCategory": ["planningCadastre"], "Free": ["parcels", "Parcels", "cadastre"]},
        licenses=["CC-BY 4.0"],
        rights=["Town of X"],
        constraints=[
            types.SimpleNamespace(type="access", constraint="Public"),
            types.SimpleNamespace(type="use", constraint="Cite the source"),
        ],
        contacts=[
            types.SimpleNamespace(name="Jane", organization="Town GIS", email="gis@town.example", position="GIS lead", addresses=["1 Main St"]),
        ],
        history=["Digitised 2019", "Updated 2026"],
        links=[types.SimpleNamespace(url="https://town.example")],
    )
    reading = isometa.read(layer(record))
    check(reading.title == "Parcels 2026", reading.title)
    check(reading.iso == {
        "abstract": "Cadastral parcels of the town.",
        "keywords": ["planningCadastre", "parcels", "cadastre"],
        "useLimitation": "CC-BY 4.0",
        "accessConstraints": "Public",
        "contact": {"organisationName": "Town GIS", "email": "gis@town.example"},
        "lineageStatement": "Digitised 2019\nUpdated 2026",
    }, reading.iso)
    check(reading.summary == "abstract, 3 keywords, licence, access constraints, contact, lineage", reading.summary)
    check(reading.report == [], reading.report)
    check("addresses" not in str(reading.iso) and "position" not in str(reading.iso), "nothing beyond the sheet")


def test_the_inspire_theme_is_read_from_an_inspire_vocabulary():
    record = Record(keywords={"GEMET - INSPIRE themes, version 1.0": ["Buildings", "Not a theme"], "Free": ["roofs"]})
    reading = isometa.read(layer(record))
    check(reading.iso["inspireTheme"] == "bu", reading.iso)
    check(reading.iso["keywords"] == ["Not a theme", "roofs"], reading.iso)
    check(any("not a registry theme" in line for line in reading.report), reading.report)
    check(reading.summary == "INSPIRE theme, 2 keywords", reading.summary)

    # A theme label outside an INSPIRE vocabulary is just a keyword.
    reading = isometa.read(layer(Record(keywords={"Free": ["Buildings"]})))
    check("inspireTheme" not in reading.iso and reading.iso["keywords"] == ["Buildings"], reading.iso)


def test_rights_stand_in_for_a_missing_licence_and_a_contact_needs_an_email():
    record = Record(
        rights=["© Town", "© Town"],
        contacts=[types.SimpleNamespace(name="Jane", organization="Town", email="")],
    )
    reading = isometa.read(layer(record))
    check(reading.iso == {"useLimitation": "© Town"}, reading.iso)
    check(any("Contact without" in line for line in reading.report), reading.report)


def test_keywords_are_capped_and_long_texts_cut():
    many = ["kw{}".format(i) for i in range(40)]
    record = Record(keywords={"Free": many}, abstract="x" * 5000)
    reading = isometa.read(layer(record))
    check(len(reading.iso["keywords"]) == 32, len(reading.iso["keywords"]))
    check(len(reading.iso["abstract"]) == 4000, len(reading.iso["abstract"]))
    check(any("32 keywords" in line for line in reading.report), reading.report)
    check(any("abstract is cut" in line for line in reading.report), reading.report)


def test_nothing_in_qgis_sends_nothing_and_says_so():
    reading = isometa.read(layer(Record(keywords={}, licenses=[], contacts=[], history=[])))
    check(reading.iso is None and reading.title is None, reading.iso)
    check(isometa.describe_metadata(reading) == "Metadata: none in QGIS; fill the item's sheet on ALIGN.", "sentence")

    # The legacy layer-level abstract still counts.
    reading = isometa.read(layer(Record(keywords={}), abstract="Old-style abstract"))
    check(reading.iso == {"abstract": "Old-style abstract"}, reading.iso)

    # A layer without a metadata record at all.
    reading = isometa.read(Record(name="no-metadata"))
    check(reading.iso is None, "no record, nothing to send")


def test_the_dialog_sentence_names_what_travels_and_what_was_cut():
    reading = isometa.Reading({"abstract": "x"}, None, "abstract, licence", ["Only the first 32 keywords are kept (40 in QGIS)."])
    check(isometa.describe_metadata(reading) == "Metadata: abstract, licence. Only the first 32 keywords are kept (40 in QGIS).", "sentence")


def test_fields_the_organization_kept_are_named_after_the_publish():
    sent = {"abstract": "x", "useLimitation": "Proprietary", "contact": {"organisationName": "Me", "email": "me@x"}}
    catalogued = {"assetId": "a1", "iso": {"abstract": "x", "useLimitation": "CC-BY 4.0"}}
    check(locked_fields_of(sent, catalogued) == ["contact"], locked_fields_of(sent, catalogued))
    check(locked_fields_of(sent, {"assetId": "a1"}) == ["licence", "contact"], "no sheet stored: both sent fields were kept away")
    check(locked_fields_of(None, catalogued) == [], "nothing sent, nothing locked")
    check(locked_fields_of({"abstract": "x"}, catalogued) == [], "no lockable field sent")


def test_an_empty_default_contact_row_is_not_reported_as_cut():
    empty_row = Record(keywords={}, contacts=[types.SimpleNamespace(name="", organization="", email="", position="", addresses=[])])
    reading = isometa.read(layer(empty_row))
    check(reading.iso is None and reading.report == [], "an untouched contact row is nothing, not a cut: {}".format(reading.report))
    half = Record(keywords={}, contacts=[types.SimpleNamespace(name="Jane", organization="", email="", position="", addresses=[])])
    reading = isometa.read(layer(half))
    check(any("Contact without" in line for line in reading.report), "a contact someone started is reported")


def main():
    tests = [
        test_the_sheet_fields_travel_and_nothing_else,
        test_the_inspire_theme_is_read_from_an_inspire_vocabulary,
        test_rights_stand_in_for_a_missing_licence_and_a_contact_needs_an_email,
        test_keywords_are_capped_and_long_texts_cut,
        test_nothing_in_qgis_sends_nothing_and_says_so,
        test_the_dialog_sentence_names_what_travels_and_what_was_cut,
        test_fields_the_organization_kept_are_named_after_the_publish,
    ]
    tests.append(test_an_empty_default_contact_row_is_not_reported_as_cut)
    for test in tests:
        test()
        print("ok  {}".format(test.__name__))
    print("{} checks passed".format(len(tests)))


if __name__ == "__main__":
    main()
