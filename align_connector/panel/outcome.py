# -*- coding: utf-8 -*-
"""What a finished publication is told, in the words of whoever decided it.

The same sentence goes to the message bar and to the Activity view, so it is
composed once, here, from the task's facts. Pure: proven by
``scripts/smoke_panel.py``.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from ..i18n import tr

SUCCESS = "success"
WARNING = "warning"
INFO = "info"


class Outcome(object):
    def __init__(self, level, text, asset_id=None):
        self.level = level
        self.text = text
        #: The item to open on ALIGN, when there is one to open.
        self.asset_id = asset_id


def outcome_of(task, succeeded):
    """Read a finished `PublishLayerTask` and say what happened."""
    name = getattr(task, "asset_name", None) or tr("Layer")
    org = getattr(task, "org_name", None) or "ALIGN"

    if succeeded:
        # A failed server-side conversion is not a success worth celebrating:
        # the file is stored, but nothing can display it. Announcing "it is now
        # in ALIGN" would be true and useless.
        if getattr(task, "derivation_error", None):
            return Outcome(
                WARNING,
                tr(
                    "“{}” was stored in {}, but ALIGN could not prepare it for display: {} "
                    "The file is safe in your Datastore."
                ).format(name, org, task.derivation_error),
                getattr(task, "asset_id", None),
            )
        if getattr(task, "created", None) is False:
            # The same service was already registered in this organization:
            # one item, reused, as the dedup by service identity decides.
            return Outcome(
                SUCCESS,
                tr("“{}” was already in {} on ALIGN; the existing reference is reused.").format(name, org),
                getattr(task, "asset_id", None),
            )
        text = tr("“{}” is now in {} on ALIGN.").format(name, org)
        if getattr(task, "created", None) is True:
            text += " " + tr("It is a live reference: ALIGN reads the service when the map opens.")
        flattened = getattr(task, "flattened_fields", None)
        if flattened:
            # The values are all there, but they changed shape. Saying so
            # beats letting someone discover it in the Datastore.
            text += " " + tr("Complex fields were stored as text: {}.").format(", ".join(flattened))
        servable = getattr(task, "servable_asset_id", None)
        if servable and getattr(task, "gis_format", None) == "pmtiles":
            # The archive was derived; ALIGN keeps the PMTiles, not the MBTiles.
            text += " " + tr("It was converted to PMTiles; the MBTiles archive itself is not kept.")
        elif servable:
            # The reprojection kept the original and produced a second asset.
            # Two objects now count against the organization's storage, and
            # the one that opens is not the one just created.
            text += " " + tr(
                "It had to be reprojected, so your original was kept and a second, "
                "display-ready copy was added; both use storage."
            )
        elif getattr(task, "gis_format", None) == "cog":
            text += " " + tr("It was converted to a Cloud-Optimized GeoTIFF.")
        locked = getattr(task, "locked_fields", None)
        if locked:
            # The organization enforces these sheet fields: the publish could
            # not set them, and saying so beats a sheet that quietly differs
            # from what QGIS holds (TCK-2202).
            text += " " + tr(
                "The {} on the item's sheet are set by your organization and were kept as is."
            ).format(", ".join(locked))
        return Outcome(SUCCESS, text, servable or getattr(task, "asset_id", None))

    if getattr(task, "was_cancelled", False):
        return Outcome(INFO, tr("Publishing cancelled. Nothing was catalogued."))
    return Outcome(WARNING, getattr(task, "error_message", None) or tr("Publishing failed."))
