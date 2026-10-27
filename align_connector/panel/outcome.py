# -*- coding: utf-8 -*-
"""What a finished publication is told, in the words of whoever decided it.

The same sentence goes to the message bar and to the Activity view, so it is
composed once, here, from the task's facts. Pure: proven by
``scripts/smoke_panel.py``.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from ..i18n import tr
from ..publish.feature_service import PENDING, READY, describe_result, layer_summary
from ..publish.notices import notice_texts

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

    if succeeded and getattr(task, "as_service", False):
        return _service_outcome(task, name, org)
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
        if getattr(task, "replaced", False):
            # Same item, new bytes: every map that showed it shows this version
            # (TCK-2218). What the publisher cannot see from QGIS — a field a
            # pop-up or a style read that is no longer there — is named.
            text = tr("“{}” was replaced in {} on ALIGN; the web maps that show it now show this version.").format(
                name, org
            )
            text += " " + _schema_sentence(getattr(task, "schema_changes", None))
            text += " " + tr("The previous version is kept on ALIGN.")
        elif getattr(task, "replaced_missing", False):
            text = tr(
                "The item “{}” was published as no longer existed on ALIGN, so it was published as a new item in {}."
            ).format(name, org)
        else:
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
            if len(locked) == 1:
                text += " " + tr(
                    "The {} on the item's sheet is set by your organization and was kept as is."
                ).format(locked[0])
            else:
                text += " " + tr(
                    "The {} on the item's sheet are set by your organization and were kept as is."
                ).format(", ".join(locked))
        return Outcome(SUCCESS, text, servable or getattr(task, "asset_id", None))

    if getattr(task, "was_cancelled", False):
        return Outcome(INFO, tr("Publishing cancelled. Nothing was catalogued."))
    return Outcome(WARNING, getattr(task, "error_message", None) or tr("Publishing failed."))


def _service_outcome(task, name, org):
    """A layer sent to become a feature service (TCK-2320): its file is on
    ALIGN, and the lot's service task asks for the service next (TCK-2346).
    No link: the file goes once the service is ready."""
    joins = getattr(task, "joins_service", None)
    if joins:
        return Outcome(INFO, tr("“{}” was sent to {}; it will be a layer of the feature service “{}”.").format(name, org, joins))
    return Outcome(INFO, tr("“{}” was sent to {}; ALIGN prepares its feature service next.").format(name, org))


def services_outcome(task, succeeded):
    """Read a finished `PublishServicesTask` (TCK-2346) and say what came of
    each service: what it holds, that ALIGN is still preparing it, or why it
    was not made. The item to open is the service, when there is one."""
    org = getattr(task, "org_name", None) or "ALIGN"
    if not succeeded:
        if getattr(task, "was_cancelled", False):
            return Outcome(INFO, tr("Publishing cancelled. No feature service was started."))
        return Outcome(WARNING, getattr(task, "error_message", None) or tr("Publishing failed."))
    services = list(getattr(task, "services", None) or [])
    ready = [service for service in services if service.state == READY]
    pending = [service for service in services if service.state == PENDING]
    missed = [service for service in services if service.state not in (READY, PENDING)]
    parts = []
    if len(services) == 1 and ready:
        parts.append(tr("“{}” is now an editable feature service in {} on ALIGN.").format(ready[0].title, org))
        parts.append(describe_result(ready[0].result))
    elif ready:
        parts.append(
            tr("1 editable feature service is ready in {} on ALIGN.").format(org)
            if len(ready) == 1
            else tr("{} editable feature services are ready in {} on ALIGN.").format(len(ready), org)
        )
        for service in ready:
            layers = service.result.get("layers") if isinstance(service.result, dict) else None
            parts.extend(layer_summary(layer) for layer in layers or [] if isinstance(layer, dict))
            parts.extend(notice_texts(service.result))
    for service in pending:
        parts.append(
            tr("“{}” was sent to {}; ALIGN is still preparing the feature service, which will appear in your Datastore.").format(
                service.title, org
            )
        )
    for service in missed:
        parts.append(
            tr("“{}” is stored in your Datastore, but it did not become a feature service: {}").format(service.title, service.reason)
        )
    follow_error = getattr(task, "follow_error", None)
    if follow_error:
        parts.append(follow_error)
    level = WARNING if missed or follow_error else (INFO if pending or not ready else SUCCESS)
    return Outcome(level, " ".join(parts), ready[0].service_id if len(ready) == 1 else None)


def _schema_sentence(changes):
    """What changed in the attribute fields, as ALIGN compared the two files;
    one sentence when nothing did, or when the format carries no schema."""
    if not isinstance(changes, dict):
        return tr("The attribute fields were not compared for this format.")
    parts = []
    added = [str(x) for x in changes.get("added") or []]
    removed = [str(x) for x in changes.get("removed") or []]
    retyped = [str(x) for x in changes.get("retyped") or []]
    if added:
        parts.append(tr("added {}").format(", ".join(added)))
    if removed:
        parts.append(tr("removed {}").format(", ".join(removed)))
    if retyped:
        parts.append(tr("retyped {}").format("; ".join(retyped)))
    if not parts:
        return tr("The attribute fields are unchanged.")
    sentence = tr("Attribute fields changed: {}.").format("; ".join(parts))
    if removed:
        # Only a field that is gone can break a pop-up or a style on ALIGN;
        # an added or retyped one is worth knowing, not worth a warning.
        sentence += " " + tr("A map that reads a removed field will need attention.")
    return sentence
