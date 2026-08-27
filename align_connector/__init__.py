# -*- coding: utf-8 -*-
"""ALIGN Connector for QGIS.

QGIS calls ``classFactory`` when it loads the plugin. The import of the plugin
class is deliberately deferred inside the function: anything imported at module
level runs while QGIS is still starting, and an error there fails the whole
plugin registry rather than this one plugin.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""


def classFactory(iface):  # noqa: N802 — name imposed by the QGIS plugin API
    """Return the plugin instance QGIS will drive.

    :param iface: the running QGIS interface, the only handle a plugin gets on
        the application (menus, toolbars, message bar, map canvas).
    """
    from .plugin import AlignConnectorPlugin

    return AlignConnectorPlugin(iface)
