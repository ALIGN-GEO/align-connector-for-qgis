#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the plugin zip QGIS installs, and the repository entry that serves it.

One script for the three places a release is made: a developer's machine, the
release workflow (which attaches what this builds to the GitHub Release) and
the beta repository on aligngeo.com (which serves the XML this prints). Three
hand-made variants would drift; the QGIS plugin manager is strict about the
zip's shape and the XML's fields, and a drift shows up as "plugin not found"
on a user's machine, far from the edit that caused it.

What the zip must be, per the QGIS plugin repository rules: one top-level
folder named like the package, holding ``metadata.txt``, ``__init__.py`` and a
``LICENSE`` file (no extension), nothing generated and nothing compiled. The
``.qm`` the plugin loads is shipped; the ``.ts`` it is compiled from is a
source for translators and stays in the repository.

    python scripts/package.py
    python scripts/package.py --repository-xml https://aligngeo.com/qgis > plugins.xml
    python scripts/package.py --repository-xml https://aligngeo.com/qgis \
        --download-base https://github.com/bdlst/align-connector-for-qgis/releases/download/v0.5.1 > plugins.xml

The second form is the beta repository since the GitHub Release is public:
the XML and the icon stay on aligngeo.com, the zip is the one the Release
workflow attached, so the site never carries a second copy that could drift.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import argparse
import configparser
import datetime
import hashlib
import pathlib
import sys
import zipfile
from xml.sax.saxutils import escape

REPO = pathlib.Path(__file__).resolve().parent.parent
PACKAGE = REPO / "align_connector"
LICENSE = REPO / "LICENSE"
DIST = REPO / "dist"

#: Files that never ship: compiled Python, translation sources, editor leftovers.
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".ts", ".autosave")
EXCLUDED_DIRS = ("__pycache__",)


def read_metadata() -> configparser.SectionProxy:
    parser = configparser.ConfigParser()
    parser.read(PACKAGE / "metadata.txt", encoding="utf-8")
    return parser["general"]


def shipped_files():
    """Every file of the package that belongs in the zip, in a stable order."""
    for path in sorted(PACKAGE.rglob("*")):
        if not path.is_file():
            continue
        if any(part in EXCLUDED_DIRS for part in path.relative_to(PACKAGE).parts):
            continue
        if path.suffix in EXCLUDED_SUFFIXES:
            continue
        yield path


def build(version: str) -> pathlib.Path:
    DIST.mkdir(exist_ok=True)
    # QGIS keys a repository entry by the file name up to its first dot
    # (``fileName.partition(".")[0]`` in pyplugin_installer), which must be
    # the plugin's folder name: hence a dot, not a dash, before the version.
    target = DIST / "align_connector.{}.zip".format(version)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in shipped_files():
            archive.write(path, "align_connector/" + path.relative_to(PACKAGE).as_posix())
        # The licence sits at the repository root for GitHub; QGIS wants it
        # inside the plugin folder. Same bytes, both places.
        archive.write(LICENSE, "align_connector/LICENSE")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (DIST / (target.name + ".sha256")).write_text("{}  {}\n".format(digest, target.name), encoding="utf-8")
    return target


def verify(target: pathlib.Path, version: str) -> None:
    """What the plugin manager checks before it installs; fail here instead."""
    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        for required in ("align_connector/metadata.txt", "align_connector/__init__.py", "align_connector/LICENSE"):
            if required not in names:
                raise SystemExit("zip is missing {}".format(required))
        if any("__pycache__" in name or name.endswith(".pyc") for name in names):
            raise SystemExit("zip carries compiled Python")
        if any(not name.startswith("align_connector/") for name in names):
            raise SystemExit("zip has entries outside the plugin folder")
        parser = configparser.ConfigParser()
        parser.read_string(archive.read("align_connector/metadata.txt").decode("utf-8"))
        if parser["general"]["version"] != version:
            raise SystemExit("zip metadata says {} but {} was built".format(parser["general"]["version"], version))


def repository_xml(general: configparser.SectionProxy, base_url: str, file_name: str, download_base: str = None) -> str:
    """The ``plugins.xml`` a QGIS plugin repository serves, with this one entry.

    The fields are those ``pyplugin_installer`` reads from an XML repository;
    the ones left out (``uploaded_by``, ``plugin_id``) are the official
    repository's own. Dates are today's: the file is regenerated per release.
    ``download_base`` is where the zip lives when it is not next to the XML
    (a GitHub Release); the plugin manager follows the redirect GitHub answers.
    """
    today = datetime.date.today().isoformat()
    base = base_url.rstrip("/")
    download = (download_base or base_url).rstrip("/")
    fields = [
        ("description", general["description"]),
        ("about", general["about"]),
        ("version", general["version"]),
        ("qgis_minimum_version", general["qgisMinimumVersion"]),
        ("qgis_maximum_version", general.get("qgisMaximumVersion", "")),
        ("homepage", general["homepage"]),
        ("file_name", file_name),
        ("icon", "{}/icon.png".format(base)),
        ("author_name", general["author"]),
        ("download_url", "{}/{}".format(download, file_name)),
        ("create_date", today),
        ("update_date", today),
        ("experimental", general.get("experimental", "False")),
        ("deprecated", general.get("deprecated", "False")),
        ("tracker", general["tracker"]),
        ("repository", general["repository"]),
        ("tags", general.get("tags", "")),
        ("changelog", general.get("changelog", "")),
        ("server", general.get("server", "False")),
    ]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<plugins>"]
    lines.append('  <pyqgis_plugin name="{}" version="{}">'.format(escape(general["name"], {'"': "&quot;"}), general["version"]))
    for tag, value in fields:
        lines.append("    <{0}>{1}</{0}>".format(tag, escape(value.strip())))
    lines.append("  </pyqgis_plugin>")
    lines.append("</plugins>")
    return "\n".join(lines) + "\n"


def main() -> int:
    arguments = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    arguments.add_argument(
        "--repository-xml",
        metavar="BASE_URL",
        help="also print plugins.xml for a repository serving the zip and icon.png under BASE_URL",
    )
    arguments.add_argument(
        "--download-base",
        metavar="URL",
        help="with --repository-xml: the zip is served under URL instead (a GitHub Release's download path)",
    )
    options = arguments.parse_args()

    general = read_metadata()
    version = general["version"]
    target = build(version)
    verify(target, version)
    digest = (DIST / (target.name + ".sha256")).read_text(encoding="utf-8").split()[0]
    print("built {} ({} bytes) sha256 {}".format(target.relative_to(REPO).as_posix(), target.stat().st_size, digest), file=sys.stderr)

    if options.repository_xml:
        # Bytes, not text: the XML declares UTF-8, and a console's own
        # encoding (cp1252 on Windows) would silently break the promise.
        sys.stdout.buffer.write(
            repository_xml(general, options.repository_xml, target.name, options.download_base).encode("utf-8")
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
