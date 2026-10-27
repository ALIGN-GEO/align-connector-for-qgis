# -*- coding: utf-8 -*-
"""Opening a feature service in QGIS (ADR-0063 §3, TCK-2320, TCK-2348).

A service layer is read and edited by QGIS' own OGC API Features provider
(``OAPIF``). The plugin signs its requests with the session the user opened
once (``auth/request_signer``, Benjamin's decision of 2026-10-19): one
consent, and a project that holds no credential at all, only the service's
address (CA-8). The layer therefore reads while the plugin is signed in; a
project reopened without a session says so and reloads after the sign-in
(CA-11).

The two layers never share a name (CA-7): the service layer is
"<title> (ALIGN)", the local original is kept, hidden, in a group "Local
originals". Both remember the service (``service_memory``), so the layer's
context menu offers to edit it at any time (CA-6). The layers of a service of
several layers open into a group named after the service (TCK-2346, CA-5).

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from qgis.core import Qgis, QgsDataProvider, QgsLayerTree, QgsMapLayerStyle, QgsProject, QgsVectorLayer

from .. import config
from ..auth.request_signer import supported
from ..i18n import tr
from . import memory, service_memory
from .feature_service import oapif_source


#: What the group of a service's layers carries: the service it shows (TCK-2346, CA-5).
GROUP_KEY = "align/serviceGroup"


class ServiceLayerUnavailable(Exception):
    """The service layer could not be opened; the message says why."""


def editing_supported():
    """Whether this QGIS can carry the plugin's signer (3.36 or later)."""
    return supported(getattr(Qgis, "QGIS_VERSION_INT", 0))


def open_service(ref, read_only=False, local_layer_id=None):
    """Add a layer of a service to the project.

    With ``local_layer_id``, the service layer takes the local layer's place
    and style, and the local layer is kept, hidden, in the "Local originals"
    group: nothing the user had is removed. When the service has a group
    (``service_group``), the layer goes into it instead, after the layers
    already there: the service's order.

    :param ref: the service layer (``service_memory.ServiceRef``)
    :param read_only: open it for reading only (the user may not edit, or asked to read)
    :returns: the service layer, added to the project
    :raises ServiceLayerUnavailable: when QGIS cannot open the service
    """
    ogc_url = config.feature_service_ogc_url(ref.org_id, ref.service_id)
    name = service_memory.service_layer_name(ref.title, read_only=bool(read_only))
    layer = QgsVectorLayer(oapif_source(ogc_url, ref.layer_id, ref.srid), name, "OAPIF")
    if not layer.isValid():
        raise ServiceLayerUnavailable(
            tr("QGIS could not open the feature service. You can add it yourself from its address: {}").format(ogc_url)
        )
    layer.setReadOnly(bool(read_only))

    project = QgsProject.instance()
    local = project.mapLayer(local_layer_id) if local_layer_id else None
    if local is not None:
        style = QgsMapLayerStyle()
        style.readFromLayer(local)
        style.writeToLayer(layer)
        service_memory.remember(local, ref, service_memory.ORIGINAL)
        # The style carries the original's custom properties: the service layer
        # is neither the Datastore item the original was published as, nor the original.
        memory.forget(layer)
    service_memory.remember(layer, ref, service_memory.SERVICE)
    project.addMapLayer(layer, False)

    root = project.layerTreeRoot()
    node = root.findLayer(local_layer_id) if local is not None else None
    group = _group_of(root, ref.service_id)
    if group is not None:
        group.addLayer(layer)
    elif node is not None:
        parent = node.parent()
        parent.insertLayer(parent.children().index(node), layer)
    else:
        root.insertLayer(0, layer)
    if node is not None:
        _keep_original(root, node)
    return layer


def service_group(service_id, title, local_layer_ids):
    """The group the layers of a service of several layers open into
    (TCK-2346, CA-5): the one already made for this service; else the group
    that holds exactly the service's originals, which the service layers then
    fill again; else a new group named after the service, where the first
    original is. The user's group is never renamed.

    :param local_layer_ids: the originals, in the service's order
    """
    root = QgsProject.instance().layerTreeRoot()
    group = _group_of(root, service_id)
    if group is not None:
        return group
    nodes = [node for node in (root.findLayer(layer_id) for layer_id in local_layer_ids) if node is not None]
    parent = nodes[0].parent() if nodes else root
    if parent is not root and _holds_exactly(parent, local_layer_ids):
        parent.setCustomProperty(GROUP_KEY, service_id)
        return parent
    group = parent.insertGroup(parent.children().index(nodes[0]) if nodes else 0, title)
    group.setCustomProperty(GROUP_KEY, service_id)
    return group


def _holds_exactly(group, layer_ids):
    children = group.children()
    return all(QgsLayerTree.isLayer(child) for child in children) and {child.layerId() for child in children} == set(layer_ids)


def _group_of(group, service_id):
    """The group made for this service, anywhere under ``group``, or ``None``."""
    for child in group.children():
        if not QgsLayerTree.isGroup(child):
            continue
        if child.customProperty(GROUP_KEY) == service_id:
            return child
        found = _group_of(child, service_id)
        if found is not None:
            return found
    return None


def _keep_original(root, node):
    """Move the local original into the "Local originals" group, hidden (CA-7)."""
    name = service_memory.originals_group_name()
    group = root.findGroup(name) or root.addGroup(name)
    if node.parent() is group:
        node.setItemVisibilityChecked(False)
        return
    moved = node.clone()
    moved.setItemVisibilityChecked(False)
    group.addChildNode(moved)
    node.parent().removeChildNode(node)
    group.setExpanded(False)


def service_layers(project=None):
    """The project's service layers, each with its service."""
    project = project or QgsProject.instance()
    found = []
    for layer in project.mapLayers().values():
        ref = service_memory.read(layer)
        if ref is not None and ref.kind == service_memory.SERVICE:
            found.append((layer, ref))
    return found


def reload_layer(layer):
    """Read a service layer again: once the session is back, an invalid one
    opens (CA-11), a valid one shows what changed on ALIGN."""
    if not layer.isValid():
        layer.setDataSource(layer.source(), layer.name(), layer.providerType(), QgsDataProvider.ProviderOptions())
    elif layer.dataProvider() is not None:
        layer.dataProvider().reloadData()
    layer.triggerRepaint()
    return layer.isValid()
