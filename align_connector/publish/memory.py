# -*- coding: utf-8 -*-
"""What a layer remembers of its own publication (TCK-2218, ADR-0058 V1-e).

After a publish lands, the layer keeps the item it became: asset, organization,
server, title, date. QGIS stores a layer's custom properties in the project
file, so the memory survives closing and reopening the project — and that is
what lets the panel say "already on ALIGN" and offer **Replace** rather than
a duplicate.

The memory is a claim, not a proof: the item may have been deleted on ALIGN,
the server address may have changed, the user may have lost the right to write
it. Each of those is answered by ALIGN at publish time, in a refusal the panel
reads; nothing here decides.

Pure where it can be: proven by ``scripts/smoke_memory.py`` on a stub layer.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from .. import config
from ..i18n import tr

#: Custom-property keys, namespaced so they never collide with another plugin's.
KEY_ASSET = "align/assetId"
KEY_ORG = "align/orgId"
KEY_ORG_NAME = "align/orgName"
KEY_API = "align/apiUrl"
KEY_TITLE = "align/title"
KEY_PUBLISHED_AT = "align/publishedAt"

_KEYS = (KEY_ASSET, KEY_ORG, KEY_ORG_NAME, KEY_API, KEY_TITLE, KEY_PUBLISHED_AT)


class Link(object):
    """The item a layer was published as."""

    def __init__(self, asset_id, org_id, org_name=None, api_url=None, title=None, published_at=None):
        self.asset_id = asset_id
        self.org_id = org_id
        self.org_name = org_name
        self.api_url = api_url
        self.title = title
        self.published_at = published_at


def remember(layer, asset_id, org_id, org_name=None, title=None, published_at=None):
    """Write the link on the layer. Overwrites a previous one: a layer is one
    item, the last it was published or replaced as."""
    setter = getattr(layer, "setCustomProperty", None)
    if not callable(setter):
        return
    setter(KEY_ASSET, asset_id)
    setter(KEY_ORG, org_id)
    setter(KEY_ORG_NAME, org_name or "")
    setter(KEY_API, config.api_url())
    setter(KEY_TITLE, title or "")
    setter(KEY_PUBLISHED_AT, published_at or "")


def read(layer):
    """The layer's link, or ``None``: never published, or published to another
    ALIGN deployment than the one the plugin points at now (a staging item is
    not a production item, whatever its id)."""
    getter = getattr(layer, "customProperty", None)
    if not callable(getter):
        return None
    asset_id = _text(getter(KEY_ASSET, ""))
    org_id = _text(getter(KEY_ORG, ""))
    api_url = _text(getter(KEY_API, ""))
    if not asset_id or not org_id:
        return None
    if api_url != config.api_url():
        return None
    return Link(
        asset_id,
        org_id,
        org_name=_text(getter(KEY_ORG_NAME, "")) or None,
        api_url=api_url,
        title=_text(getter(KEY_TITLE, "")) or None,
        published_at=_text(getter(KEY_PUBLISHED_AT, "")) or None,
    )


def forget(layer):
    """Remove the link: the item is gone on ALIGN, or the layer was published anew."""
    remover = getattr(layer, "removeCustomProperty", None)
    if not callable(remover):
        return
    for key in _KEYS:
        remover(key)


def describe(link):
    """One sentence for the briefing: where the layer already is."""
    org = link.org_name or link.org_id
    when = (link.published_at or "")[:10]
    if when:
        return tr("Already on ALIGN: published in {} on {}.").format(org, when)
    return tr("Already on ALIGN: published in {}.").format(org)


def _text(value):
    return value.strip() if isinstance(value, str) else ""
