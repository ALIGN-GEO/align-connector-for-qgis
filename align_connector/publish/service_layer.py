# -*- coding: utf-8 -*-
"""Opening a feature service in QGIS, in place of the layer it came from
(ADR-0063 §3, TCK-2320).

Once ALIGN has made a layer a feature service, the plugin offers to swap the
local layer for the service's: the same features, edited live on ALIGN through
OGC API Features (QGIS' ``OAPIF`` provider, which reads and edits).

**How the layer is signed.** By QGIS' own OAuth2 method, not by a copy of the
plugin's token. The plugin's token lives an hour and is refreshed by the
plugin; a layer saved in a project must keep working without the plugin
running, after the hour, and when the project is reopened next week. QGIS'
method does exactly that: it runs the authorization code flow with PKCE once,
in the browser, keeps the tokens in its encrypted credential store and
refreshes them itself. The project only ever holds the configuration's id,
never a token (rule 23, secret out of artifact). Its grant is narrower than
the plugin's: features only.

PKCE arrived in that method with QGIS 3.34; on an older QGIS the plugin gives
the service's address instead of a button that would fail.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import re

from qgis.core import Qgis, QgsApplication, QgsAuthMethodConfig, QgsMapLayerStyle, QgsProject, QgsVectorLayer
from qgis.PyQt.QtCore import QSettings

from .. import config
from ..i18n import tr
from .feature_service import can_edit_in_qgis, oapif_source, oauth2_settings

_METHOD = "OAuth2"


class ServiceLayerUnavailable(Exception):
    """The service layer could not be opened; the message says why."""


def editing_supported():
    """Whether this QGIS can open a service layer with its own sign-in."""
    if not can_edit_in_qgis(getattr(Qgis, "QGIS_VERSION_INT", 0)):
        return False
    return _METHOD in QgsApplication.authManager().authMethodsKeys()


def _deployment_key():
    """One configuration per ALIGN deployment: its endpoints are in it."""
    return re.sub(r"[^A-Za-z0-9]+", "_", "{} {}".format(config.app_url(), config.api_url())).strip("_")


def service_authcfg():
    """The authentication configuration that signs the service layers.

    Created once per deployment and reused: every service layer of every
    project shares one sign-in. One the user deleted from QGIS' manager is
    created again.
    """
    manager = QgsApplication.authManager()
    settings = QSettings()
    key = "{}/feature_service_authcfg/{}".format(config.SETTINGS_GROUP, _deployment_key())
    known = str(settings.value(key, "") or "")
    if known and known in manager.configIds():
        return known

    if not manager.masterPasswordIsSet() and not manager.setMasterPassword(True):
        raise ServiceLayerUnavailable(
            tr("QGIS' password manager must be unlocked to keep the sign-in of the feature-service layers.")
        )
    name = tr("ALIGN feature services")
    cfg = QgsAuthMethodConfig(_METHOD)
    cfg.setName(name)
    cfg.setUri(config.api_url())
    cfg.setConfig(
        "oauth2config",
        oauth2_settings(
            name, config.authorize_url(), config.token_endpoint(), config.CLIENT_ID, config.SERVICE_LAYER_SCOPES
        ),
    )
    stored = manager.storeAuthenticationConfig(cfg)
    # The binding returns a bool and fills the id in place; some builds
    # return the (bool, config) pair instead.
    if isinstance(stored, tuple):
        stored, cfg = stored[0], stored[-1]
    if not stored or not cfg.id():
        raise ServiceLayerUnavailable(tr("QGIS could not store the sign-in of the feature-service layers."))
    settings.setValue(key, cfg.id())
    return cfg.id()


def open_in_place(local_layer_id, title, ogc_url, collection_id, srid=None):
    """Add the service's layer where the local layer is, with its style.

    The local layer stays in the project, hidden under the new one: nothing
    the user had is removed, and they can delete it once they trust the
    service. Fields ALIGN renamed at publication may leave a style expression
    pointing at a name that is gone; the layer still opens.

    :returns: the service layer, added to the project
    :raises ServiceLayerUnavailable: when QGIS cannot open the service
    """
    authcfg = service_authcfg()
    layer = QgsVectorLayer(oapif_source(ogc_url, collection_id, authcfg, srid), title, "OAPIF")
    if not layer.isValid():
        raise ServiceLayerUnavailable(
            tr("QGIS could not open the feature service. You can add it yourself from its address: {}").format(ogc_url)
        )

    project = QgsProject.instance()
    local = project.mapLayer(local_layer_id)
    if local is not None:
        style = QgsMapLayerStyle()
        style.readFromLayer(local)
        style.writeToLayer(layer)
    project.addMapLayer(layer, False)

    root = project.layerTreeRoot()
    node = root.findLayer(local_layer_id) if local is not None else None
    if node is not None:
        parent = node.parent()
        parent.insertLayer(parent.children().index(node), layer)
        node.setItemVisibilityChecked(False)
    else:
        root.insertLayer(0, layer)
    return layer
