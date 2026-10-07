# -*- coding: utf-8 -*-
"""Sentences the publisher reads, built from facts: pure, and proven as such.

Sizes, capacity and style are facts the API or the converter hand back; what
a person sees is a sentence. Keeping the sentences here, away from any
widget, is what lets the smoke scripts read them on a plain interpreter.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from ..i18n import tr


def readable_size(size_bytes):
    """A size a person reads at a glance, or None when it is unknown."""
    if not size_bytes:
        return None
    units = ("bytes", "KB", "MB", "GB", "TB")
    value = float(size_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return "{:.0f} {}".format(value, unit) if unit == "bytes" else "{:.1f} {}".format(value, unit)
        value /= 1024.0
    return None


def describe_capacity(capacity_info):
    """One line a publisher reads under the organization picker (TCK-2179).

    ``Storage 1.2 GB of 10.0 GB used · 844 credits``. An unlimited storage says
    so; a figure ALIGN did not give is left out rather than guessed; nothing at
    all gives an empty string, and the label simply stays blank.
    """
    parts = []
    storage = capacity_info.get("storage") or {}
    used = storage.get("usedBytes")
    limit = storage.get("limitBytes")
    if used is not None:
        used_text = readable_size(used) or "0 bytes"
        if limit:
            parts.append(tr("Storage {} of {} used").format(used_text, readable_size(limit)))
        else:
            parts.append(tr("Storage {} used, no limit").format(used_text))
    available = (capacity_info.get("credits") or {}).get("available")
    if available is not None:
        parts.append((tr("{} credit") if available == 1 else tr("{} credits")).format(available))
    return " · ".join(parts)


def describe_style(conversion):
    """What the publisher reads about the style before sending (TCK-2201).

    ``Style: categorized on zone, 4 classes. Simplified: hatched fill rendered
    flat.`` A layer that takes ALIGN's default style says so.
    """
    if conversion.style is None:
        head = tr("Style: ALIGN's default will apply.")
    else:
        head = tr("Style: {}.").format(conversion.summary or tr("transmitted"))
    if conversion.report:
        head += " " + tr("Simplified:") + " " + " ".join(conversion.report)
    return head
