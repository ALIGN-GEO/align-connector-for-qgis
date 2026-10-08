# -*- coding: utf-8 -*-
"""Reading a QGIS layer source: QGIS's own decoder first, a plain reading after.

A QGIS source is one of two dialects. Tile and vector-tile sources are a
query string (``type=xyz&url=https://…&zmax=19``); feature-service sources
are a data-source URI (``url='https://…' typename='ns:parcels'``). QGIS
decodes both through `QgsProviderRegistry.decodeUri` (rule 14), which older
builds lack for some providers: the plain reading below is the fallback, and
what the smokes prove.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import re

from qgis.core import QgsProviderRegistry

_QUOTED = re.compile(r"""(\w[\w\-:]*)=('((?:[^']|'')*)'|"((?:[^"]|"")*)"|(\S*))""")


def decode(provider, source):
    """What QGIS reads in the source, as a dict of strings (lists for repeated
    keys such as WMS layers). Never raises."""
    decoded = _registry(provider, source)
    if decoded:
        return {key: _plain(value) for key, value in decoded.items()}
    return parse(source or "")


def parse(source):
    """The plain reading, pure: a query string or a data-source URI."""
    source = (source or "").strip()
    if not source:
        return {}
    if "='" in source or '="' in source:
        return _parse_datasource(source)
    return _parse_query(source)


def _parse_query(source):
    decoded = {}
    for part in source.split("&"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = _unescape(value.strip())
        if key in decoded:
            existing = decoded[key]
            decoded[key] = (existing if isinstance(existing, list) else [existing]) + [value]
        else:
            decoded[key] = value
    return decoded


def _parse_datasource(source):
    decoded = {}
    for match in _QUOTED.finditer(source):
        key = match.group(1)
        value = match.group(3) if match.group(3) is not None else match.group(4) if match.group(4) is not None else match.group(5)
        decoded[key] = (value or "").replace("''", "'").replace('""', '"')
    return decoded


def _unescape(value):
    """QGIS percent-encodes a URL inside a query-string source."""
    if "%" not in value:
        return value
    try:
        from urllib.parse import unquote

        return unquote(value)
    except Exception:  # noqa: BLE001 — a value that will not decode is used as written
        return value


def _registry(provider, source):
    try:
        return QgsProviderRegistry.instance().decodeUri(provider, source) or {}
    except Exception:  # noqa: BLE001 — a provider without a decoder reads through the plain parser
        return {}


def _plain(value):
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    if value is None:
        return ""
    return str(value)
