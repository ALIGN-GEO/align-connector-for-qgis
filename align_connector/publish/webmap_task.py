# -*- coding: utf-8 -*-
"""Asking ALIGN for the web map, off the interface thread.

One request, a few kilobytes; still a round trip to the network, and the
interface never waits on one. A ``QgsTask`` like the publications, with the
same reading of the answer: ALIGN decides, this reports.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from qgis.core import QgsTask

from .. import api


class ComposeWebMapTask(QgsTask):
    """Create the web map that shows the published layers (TCK-2204)."""

    def __init__(self, session, org_id, org_name, name, layers, map_view=None, description=None, visibility=None):
        super().__init__("Creating web map “{}” on ALIGN".format(name))
        self.session = session
        self.org_id = org_id
        self.org_name = org_name
        self.name = name
        self.layers = layers
        self.map_view = map_view
        self.description = description
        self.visibility = visibility

        #: Set on completion, read by whoever connected to `taskCompleted`.
        self.asset_id = None
        self.placed = []
        self.skipped = []
        self.error_message = None
        self.needs_sign_in = False

    def run(self):
        try:
            answer = api.create_web_map(
                self.session,
                self.org_id,
                self.name,
                self.layers,
                map_view=self.map_view,
                description=self.description,
                visibility=self.visibility,
            )
        except api.ApiError as exc:
            self.error_message = exc.message
            self.needs_sign_in = exc.needs_sign_in
            return False
        self.asset_id = answer["assetId"]
        self.placed = answer["layers"]
        # The server names the skipped items by id; the titles are ours.
        titles = {entry["assetId"]: entry.get("title") for entry in self.layers}
        self.skipped = [dict(entry, title=titles.get(entry.get("assetId"))) for entry in answer["skipped"]]
        self.setProgress(100)
        return True

    def finished(self, result):
        """Runs on the interface thread. Kept empty on purpose (see PublishLayerTask)."""
        return
