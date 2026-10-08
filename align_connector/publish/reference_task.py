# -*- coding: utf-8 -*-
"""Registering a reference, off the interface thread (TCK-2207).

No bytes leave: one request tells ALIGN what the service is, and ALIGN
answers with the item, created or found. A ``QgsTask`` like the others so the
Activity view follows it the same way.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from qgis.core import QgsTask

from .. import api
from ..metadata import plugin_version


class RegisterReferenceTask(QgsTask):
    """Register one service layer as a live reference."""

    def __init__(self, session, layer_id, org_id, org_name, asset_name, body, description=None, visibility=None):
        super().__init__("Registering “{}” on ALIGN".format(asset_name))
        self.session = session
        self.layer_id = layer_id
        self.org_id = org_id
        self.org_name = org_name
        self.asset_name = asset_name
        self.body = body
        self.description = description
        self.visibility = visibility

        #: Set on completion, read by whoever connected to `taskCompleted`.
        self.asset_id = None
        self.created = None
        self.error_message = None
        self.needs_sign_in = False
        #: What the other tasks carry, kept so one outcome reader serves all.
        self.servable_asset_id = None
        self.derivation_error = None
        self.gis_format = None
        self.flattened_fields = []
        self.locked_fields = []
        self.was_cancelled = False

    def run(self):
        try:
            answer = api.create_reference(
                self.session,
                self.org_id,
                self.asset_name,
                self.body,
                provenance=self._provenance(),
                description=self.description,
                visibility=self.visibility,
            )
        except api.ApiError as exc:
            self.error_message = exc.message
            self.needs_sign_in = exc.needs_sign_in
            return False
        self.asset_id = answer["assetId"]
        self.created = answer["created"]
        self.gis_format = answer.get("gisFormat")
        self.setProgress(100)
        return True

    def finished(self, result):
        """Runs on the interface thread. Kept empty on purpose (see PublishLayerTask)."""
        return

    def _provenance(self):
        from qgis.core import Qgis

        from .task import utc_now

        return {
            "source": "qgis-plugin",
            "sourceLayerId": self.layer_id,
            "clientVersion": plugin_version(),
            "appVersion": Qgis.QGIS_VERSION.split("-")[0],
            "publishedAt": utc_now(),
        }
