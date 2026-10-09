#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate ``align_connector/metadata.txt`` against what QGIS requires.

The QGIS plugin repository rejects a submission whose metadata is incomplete,
and QGIS itself refuses to load a plugin missing a mandatory field. Both
failures happen far from the edit that caused them, so this check runs in CI.

It deliberately validates *shape*, not content: the fields QGIS reads, the
version format, and the couple of values that carry a promise (the license we
are bound to, and the minimum QGIS version we claim to support).

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import argparse
import configparser
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
METADATA = REPO / "align_connector" / "metadata.txt"
LICENSE = REPO / "LICENSE"

# Required by QGIS to load the plugin at all.
REQUIRED = (
    "name",
    "qgisMinimumVersion",
    "description",
    "version",
    "author",
    "email",
    "about",
    "repository",
)

# Required by the QGIS plugin repository to accept a submission: links that
# must resolve, a licence it can read, a changelog it shows per version.
REQUIRED_FOR_REPOSITORY = ("homepage", "tracker", "license", "changelog")

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
QGIS_VERSION_RE = re.compile(r"^\d+\.\d+$")

# Linking PyQGIS makes the plugin a derivative work: the licence is not a
# preference, and a change here is a legal change, not a metadata edit.
EXPECTED_LICENSE = "GPL-2.0-or-later"


def main() -> int:
    arguments = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    arguments.add_argument(
        "--tag",
        help="a git tag (vX.Y.Z) that must name the same version as metadata.txt",
    )
    options = arguments.parse_args()

    if not METADATA.exists():
        print(f"missing {METADATA}")
        return 1

    parser = configparser.ConfigParser()
    parser.read(METADATA, encoding="utf-8")

    if not parser.has_section("general"):
        print("metadata.txt has no [general] section")
        return 1

    general = parser["general"]
    problems = []

    for field in REQUIRED:
        if not general.get(field, "").strip():
            problems.append(f"missing or empty required field: {field}")
    for field in REQUIRED_FOR_REPOSITORY:
        if not general.get(field, "").strip():
            problems.append(f"missing or empty field the plugin repository requires: {field}")

    # The repository wants the licence next to metadata.txt; package.py copies
    # it there from the repository root, which therefore must hold it.
    if not LICENSE.exists():
        problems.append("LICENSE (no extension) is missing at the repository root")

    version = general.get("version", "")
    if version and not VERSION_RE.match(version):
        problems.append(f"version must be X.Y.Z, got {version!r}")
    # A release is cut from a tag; a tag that names another version than the
    # metadata ships a zip QGIS will label wrongly, and the repository
    # refuses a version it has already seen.
    if options.tag and options.tag != f"v{version}":
        problems.append(f"tag {options.tag!r} does not match metadata version {version!r} (expected v{version})")

    minimum = general.get("qgisMinimumVersion", "")
    if minimum and not QGIS_VERSION_RE.match(minimum):
        problems.append(f"qgisMinimumVersion must be X.Y, got {minimum!r}")

    license_value = general.get("license", "")
    if license_value != EXPECTED_LICENSE:
        problems.append(
            f"license must be {EXPECTED_LICENSE!r} (PyQGIS is GPL), got {license_value!r}"
        )

    icon = general.get("icon", "").strip()
    if icon and not (METADATA.parent / icon).exists():
        problems.append(f"icon {icon!r} declared but not found next to metadata.txt")

    if problems:
        for problem in problems:
            print(f"metadata.txt: {problem}")
        return 1

    print(f"metadata.txt OK — {general['name']} {version} (QGIS >= {minimum})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
