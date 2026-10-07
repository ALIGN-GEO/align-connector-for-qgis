# -*- coding: utf-8 -*-
"""The layer's metadata, carried to the item's ISO sheet on ALIGN.

QGIS keeps a full ISO 19115 record on a layer (``QgsLayerMetadata``): title,
abstract, keywords by vocabulary, licences, rights, constraints, contacts,
links, history. ALIGN's item sheet edits a deliberate subset of it — abstract,
keywords, licence, access constraints, one contact, an INSPIRE theme, a
lineage statement — and that subset is all this module sends: nothing the
sheet does not show ever leaves QGIS (data minimisation).

Written against the QGIS API by names (methods on the record, attributes on
its contacts and constraints), so the reading is proven on a plain
interpreter with stand-ins (``scripts/smoke_isometa.py``); QGIS is never
imported here.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""

#: ALIGN's ISO sheet keeps this many keywords at most.
MAX_KEYWORDS = 32

#: ALIGN's text fields keep this many characters at most.
MAX_TEXT = 4000

#: The INSPIRE registry themes ALIGN knows, English label (lower-cased) → id.
#: The same list as the platform's `IsoMetadata.ts`; a label found in a QGIS
#: INSPIRE vocabulary becomes the item's theme, anything else stays a keyword.
INSPIRE_THEMES = {
    "coordinate reference systems": "rs",
    "geographical grid systems": "gg",
    "geographical names": "gn",
    "administrative units": "au",
    "addresses": "ad",
    "cadastral parcels": "cp",
    "transport networks": "tn",
    "hydrography": "hy",
    "protected sites": "ps",
    "elevation": "el",
    "land cover": "lc",
    "orthoimagery": "oi",
    "geology": "ge",
    "statistical units": "su",
    "buildings": "bu",
    "soil": "so",
    "land use": "lu",
    "human health and safety": "hh",
    "utility and governmental services": "us",
    "environmental monitoring facilities": "ef",
    "production and industrial facilities": "pf",
    "agricultural and aquaculture facilities": "af",
    "population distribution — demography": "pd",
    "population distribution - demography": "pd",
    "area management/restriction/regulation zones and reporting units": "am",
    "natural risk zones": "nz",
    "atmospheric conditions": "ac",
    "meteorological geographical features": "mf",
    "oceanographic geographical features": "of",
    "sea regions": "sr",
    "bio-geographical regions": "br",
    "habitats and biotopes": "hb",
    "species distribution": "sd",
    "energy resources": "er",
    "mineral resources": "mr",
}

#: Constraint types QGIS writes (free text) that mean "who may access this".
_ACCESS_TYPES = ("access",)


class Reading(object):
    """What the layer's metadata becomes on the sheet.

    :param iso: the fields to send, or ``None`` when QGIS holds nothing usable
    :param title: the record's title, to propose as the item's name, or ``None``
    :param summary: one short phrase for the dialog ("abstract, 5 keywords, licence")
    :param report: what could not travel, one sentence each
    """

    def __init__(self, iso, title, summary, report):
        self.iso = iso
        self.title = title
        self.summary = summary
        self.report = list(report)


def read(layer):
    """Read the layer's metadata record. Never raises: a record that cannot be
    read is simply nothing to send."""
    record = _call(layer, "metadata", None)
    if record is None:
        return Reading(None, None, None, [])

    report = []
    iso = {}
    parts = []

    abstract = _text(_call(record, "abstract", "")) or _text(_call(layer, "abstract", ""))
    if abstract:
        iso["abstract"] = _bounded(abstract, "abstract", report)
        parts.append("abstract")

    keywords, theme = _keywords_and_theme(_call(record, "keywords", {}) or {}, report)
    if theme:
        iso["inspireTheme"] = theme
        parts.append("INSPIRE theme")
    if keywords:
        iso["keywords"] = keywords
        parts.append("{} keyword{}".format(len(keywords), "" if len(keywords) == 1 else "s"))

    licence = _joined(_call(record, "licenses", []) or []) or _joined(_call(record, "rights", []) or [])
    if licence:
        iso["useLimitation"] = _bounded(licence, "licence", report)
        parts.append("licence")

    access = _joined(
        _get(c, "constraint") for c in (_call(record, "constraints", []) or [])
        if _text(_get(c, "type")).lower() in _ACCESS_TYPES
    )
    if access:
        iso["accessConstraints"] = _bounded(access, "access constraints", report)
        parts.append("access constraints")

    contact = _contact(_call(record, "contacts", []) or [], report)
    if contact:
        iso["contact"] = contact
        parts.append("contact")

    history = _joined(_call(record, "history", []) or [], separator="\n")
    if history:
        iso["lineageStatement"] = _bounded(history, "lineage", report)
        parts.append("lineage")

    title = _text(_call(record, "title", "")) or None
    if not iso:
        return Reading(None, title, None, report)
    return Reading(iso, title, ", ".join(parts), report)


def describe_metadata(reading):
    """What the publisher reads about the metadata before sending (TCK-2202)."""
    if reading.iso is None:
        head = "Metadata: none in QGIS; fill the item's sheet on ALIGN."
    else:
        head = "Metadata: {}.".format(reading.summary)
    if reading.report:
        head += " " + " ".join(reading.report)
    return head


# ---------------------------------------------------------------------------
# Readers, pure and defensive
# ---------------------------------------------------------------------------

def _keywords_and_theme(vocabularies, report):
    """All keywords, deduplicated across vocabularies, plus the INSPIRE theme
    when an INSPIRE vocabulary names one ALIGN knows."""
    keywords = []
    theme = None
    seen = set()
    for vocabulary, words in (vocabularies.items() if hasattr(vocabularies, "items") else []):
        is_inspire = "inspire" in _text(vocabulary).lower()
        for word in words or []:
            word = _text(word)
            if not word:
                continue
            if is_inspire and theme is None and word.lower() in INSPIRE_THEMES:
                theme = INSPIRE_THEMES[word.lower()]
                continue
            if is_inspire and word.lower() not in INSPIRE_THEMES:
                report.append("INSPIRE keyword '{}' is not a registry theme; kept as a keyword.".format(word))
            if word.lower() in seen:
                continue
            seen.add(word.lower())
            keywords.append(word[:MAX_TEXT])
    if len(keywords) > MAX_KEYWORDS:
        report.append("Only the first {} keywords are kept ({} in QGIS).".format(MAX_KEYWORDS, len(keywords)))
        keywords = keywords[:MAX_KEYWORDS]
    return keywords, theme


def _contact(contacts, report):
    """The first contact with both an organisation (or a name) and an email —
    the two fields the sheet shows. Nothing else of a person travels."""
    for contact in contacts:
        organisation = _text(_get(contact, "organization")) or _text(_get(contact, "name"))
        email = _text(_get(contact, "email"))
        if organisation and "@" in email:
            return {"organisationName": organisation[:MAX_TEXT], "email": email[:320]}
    if contacts:
        report.append("Contact without an organisation and an email: not transmitted.")
    return None


def _joined(values, separator="; "):
    cleaned = []
    for value in values:
        text = _text(value)
        if text and text not in cleaned:
            cleaned.append(text)
    return separator.join(cleaned)


def _bounded(text, what, report):
    if len(text) > MAX_TEXT:
        report.append("The {} is cut to {} characters.".format(what, MAX_TEXT))
        return text[:MAX_TEXT]
    return text


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _get(target, name):
    """An attribute, or a method's result: QGIS exposes contacts and
    constraints as plain attributes, records as methods."""
    if target is None:
        return None
    value = getattr(target, name, None)
    if callable(value):
        try:
            return value()
        except Exception:  # noqa: BLE001 — a binding gap is a missing field, not a crash
            return None
    return value


def _call(target, name, default=None):
    if target is None:
        return default
    method = getattr(target, name, None)
    if not callable(method):
        return default
    try:
        return method()
    except Exception:  # noqa: BLE001 — see _get
        return default
