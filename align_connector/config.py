# -*- coding: utf-8 -*-
"""Where the plugin talks to, and under which identity.

**No infrastructure hostname belongs here.** ALIGN runs on one cloud today and
plans to move to another (ADR-0045). A plugin installed on a user's machine
outlives that migration: if it held a provider URL — a Cloud Run revision, a
storage bucket host — every installed copy would break the day the platform
moves, and only a plugin update could fix it.

So the plugin knows **ALIGN domain names**, which are repointed at migration
without anyone reinstalling anything. The same discipline applies to the upload
step: the URL to PUT bytes to is handed back by the API and treated as opaque,
never constructed here.

Both endpoints stay overridable through QSettings — for a test environment, or
for a self-hosted ALIGN.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from qgis.PyQt.QtCore import QSettings

#: Root of every QSettings key this plugin owns.
SETTINGS_GROUP = "align-connector"

#: Client identity, as allowlisted server-side. Not a secret: a public OAuth
#: client is identified, never authenticated — PKCE is what proves the exchange
#: comes from the same client that started it (RFC 8252 §8.5).
CLIENT_ID = "align-connector-qgis"

#: The only scope v1 grants. Publishing, and nothing else — this token cannot
#: read or download the organization's data.
SCOPES = ("datastore:publish",)

# Product domains, deliberately, not the hosts they currently resolve to. The web
# application lives under aligngeo.com (/login, /app, /oauth/authorize); the API
# has its own host.
DEFAULT_APP_URL = "https://aligngeo.com"
DEFAULT_API_URL = "https://api.aligngeo.com"


def _setting(key, default):
    value = QSettings().value("{}/{}".format(SETTINGS_GROUP, key), default)
    text = str(value).strip() if value is not None else ""
    return (text or default).rstrip("/")


def app_url():
    """Base URL of the ALIGN web application (where consent is given)."""
    return _setting("app_url", DEFAULT_APP_URL)


def api_url():
    """Base URL of the ALIGN API."""
    return _setting("api_url", DEFAULT_API_URL)


def set_endpoints(app, api):
    """Point the plugin at another ALIGN deployment.

    Used by the settings UI, and by anyone running against an environment whose
    product domains are not mapped yet.
    """
    settings = QSettings()
    settings.setValue("{}/app_url".format(SETTINGS_GROUP), app.rstrip("/"))
    settings.setValue("{}/api_url".format(SETTINGS_GROUP), api.rstrip("/"))


def authorize_url():
    """The consent page a browser opens. Not an API endpoint: a human approves here."""
    return "{}/oauth/authorize".format(app_url())


def client_endpoint():
    """Describe this client — the only call that needs no session.

    Used by the settings screen to answer three questions at once: the address
    resolves, it is an ALIGN, and this plugin is allowlisted on it.
    """
    return "{}/api/v1/oauth/clients/{}".format(api_url(), CLIENT_ID)


def token_endpoint():
    return "{}/api/v1/oauth/token".format(api_url())


def revoke_endpoint():
    return "{}/api/v1/oauth/revoke".format(api_url())


def orgs_endpoint():
    return "{}/api/v1/orgs".format(api_url())


def capacity_endpoint(org_id):
    """What the organization can still spend: storage and credits (TCK-2179)."""
    return "{}/api/v1/orgs/{}/capacity".format(api_url(), org_id)


def item_url(org_id, asset_id):
    """The item's page in the ALIGN web application — where everything QGIS
    does not redo (sharing, widgets, billing) lives (ADR-0058 §5)."""
    return "{}/app/{}/datastore/{}".format(app_url(), org_id, asset_id)


def upload_url_endpoint(org_id):
    return "{}/api/v1/orgs/{}/datastore/upload-url".format(api_url(), org_id)


def upload_session_endpoint(org_id):
    """Resumable transfer — the door this plugin always uses.

    A small file simply travels in one chunk. One path, tested once, beats two
    that drift apart.
    """
    return "{}/api/v1/orgs/{}/datastore/upload-session".format(api_url(), org_id)


def finalize_endpoint(org_id, asset_id):
    return "{}/api/v1/orgs/{}/datastore/{}/finalize".format(api_url(), org_id, asset_id)


def replace_session_endpoint(org_id, asset_id):
    """Replace a published item's bytes in place, step 1 of 2 (TCK-2218)."""
    return "{}/api/v1/orgs/{}/datastore/{}/replace-session".format(api_url(), org_id, asset_id)


def replace_finalize_endpoint(org_id, asset_id):
    """Replace a published item's bytes in place, step 2 of 2 (TCK-2218)."""
    return "{}/api/v1/orgs/{}/datastore/{}/replace-finalize".format(api_url(), org_id, asset_id)


def scope_string():
    """Scopes as the wire format expects them — space-delimited (RFC 6749 §3.3)."""
    return " ".join(SCOPES)


def web_map_endpoint(org_id):
    """Compose a web map from published items (TCK-2204)."""
    return "{}/api/v1/orgs/{}/datastore/web-maps/from-assets".format(api_url(), org_id)


def web_map_url(org_id, asset_id):
    """The web map in the Map builder."""
    return "{}/app/{}/map-viewer?webmap={}".format(app_url(), org_id, asset_id)


def references_endpoint(org_id):
    """Register a web-service layer as a live reference (TCK-2207)."""
    return "{}/api/v1/orgs/{}/datastore/references".format(api_url(), org_id)
