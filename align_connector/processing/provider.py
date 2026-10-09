# -*- coding: utf-8 -*-
"""The « ALIGN » provider in the Processing toolbox (TCK-2181).

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .publish_layer import PublishLayerAlgorithm

#: The prefix of every algorithm id: ``align:publishlayer``.
PROVIDER_ID = "align"


class AlignProcessingProvider(QgsProcessingProvider):
    """Holds the plugin's session so every algorithm run signs as the user."""

    def __init__(self, session, plugin_dir):
        super().__init__()
        self._session = session
        self._plugin_dir = plugin_dir

    def id(self):  # noqa: A003 — name imposed by the QGIS API
        return PROVIDER_ID

    def name(self):
        return "ALIGN"

    def longName(self):  # noqa: N802 — name imposed by the QGIS API
        return "ALIGN Connector"

    def icon(self):
        return QIcon(os.path.join(self._plugin_dir, "icon.png"))

    def svgIconPath(self):  # noqa: N802 — name imposed by the QGIS API
        return os.path.join(self._plugin_dir, "icon.png")

    def loadAlgorithms(self):  # noqa: N802 — name imposed by the QGIS API
        self.addAlgorithm(PublishLayerAlgorithm(self._session))
