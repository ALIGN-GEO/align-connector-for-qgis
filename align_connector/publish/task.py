# -*- coding: utf-8 -*-
"""Publishing a layer, off the interface thread.

A publish is export, then transfer, then catalogue. The transfer is the long
part — minutes for a modest layer, hours for a raster — so the whole thing runs
in a ``QgsTask``: QGIS keeps its progress bar, its cancel button, and its
responsiveness, and the user keeps working on the map meanwhile.

What the task never does is decide whether the user is allowed to publish.
ALIGN decides; this reports what it answered.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import os
import shutil

from qgis.core import QgsTask

from .. import api
from ..metadata import plugin_version
from .exporter import ExportFailed, export_for_publish
from .uploader import ResumableUploader, UploadCancelled, UploadFailed

#: How the export is weighted against the transfer in the progress bar. Writing
#: the file is quick; sending it is what takes the time.
_EXPORT_SHARE = 5.0

#: The two refusals that are about what the organization can still spend:
#: credits for a conversion, space for the file.
_CAPACITY_REFUSALS = (402, 413)


#: The sheet fields an organization may enforce, with the words the user reads.
LOCKABLE_FIELDS = (("useLimitation", "licence"), ("accessConstraints", "access constraints"), ("contact", "contact"))


def locked_fields_of(sent, catalogued):
    """The human names of the metadata fields sent but kept as the organization
    has them (TCK-2202): present in what was sent, absent from what ALIGN
    catalogued. Empty when nothing was sent or the answer is unreadable."""
    if not sent or not isinstance(catalogued, dict):
        return []
    stored = catalogued.get("iso")
    if not isinstance(stored, dict):
        stored = {}
    return [label for field, label in LOCKABLE_FIELDS if sent.get(field) is not None and field not in stored]


def with_capacity(message, status, capacity_line):
    """Add what the organization had to a refusal that is about capacity.

    A 402 or a 413 already says what was missing; the line the dialog showed
    says what there was, so the two read together (TCK-2179). Any other refusal
    is passed through untouched.
    """
    if status in _CAPACITY_REFUSALS and capacity_line:
        return "{} The organization currently has: {}.".format(message, capacity_line)
    return message


class PublishLayerTask(QgsTask):
    """Export one layer, send it, and catalogue it in ALIGN."""

    def __init__(
        self,
        session,
        layer,
        org_id,
        org_name,
        asset_name,
        selected_only=False,
        description=None,
        visibility=None,
        capacity_line=None,
        style=None,
        iso=None,
    ):
        super().__init__("Publishing “{}” to ALIGN".format(asset_name), QgsTask.CanCancel)
        self.session = session
        self.layer = layer
        self.org_id = org_id
        self.org_name = org_name
        self.asset_name = asset_name
        self.selected_only = selected_only
        self.description = description
        self.visibility = visibility
        #: What the dialog showed about the organization's remaining space and
        #: credits, repeated in a refusal that is about either of them.
        self.capacity_line = capacity_line
        #: The layer's symbology, converted on the interface thread by the
        #: dialog (QGIS layers are not for other threads to read), sent at
        #: finalize as the item's default style (TCK-2201).
        self.style = style
        #: The layer's metadata for the item's ISO sheet (TCK-2202), read on
        #: the interface thread by the dialog.
        self.iso = iso
        #: Metadata fields the organization enforces, which the publish could
        #: not set: read off the catalogued asset, told to the user.
        self.locked_fields = []
        self.layer_id = layer.id()

        #: Set on completion, read by whoever connected to `taskCompleted`.
        self.error_message = None
        self.needs_sign_in = False
        self.was_cancelled = False
        self.asset_id = None
        #: Fields serialised to JSON text to make the layer writable. Reported
        #: to the user: a value that changed shape should not do so silently.
        self.flattened_fields = []
        #: What ALIGN did with the bytes once they landed. A raster is normalised
        #: server-side, and the outcome changes what the user should be told —
        #: including, in one case, that the quota now holds two objects.
        self.servable_asset_id = None
        self.derivation_error = None
        self.gis_format = None
        #: The layer's own coordinate system, recorded in the provenance so the
        #: origin of a reprojected file is never lost (TCK-2199).
        self.source_crs = None

        self._workdir = None

    # -- worker thread ------------------------------------------------------

    def run(self):
        try:
            export = export_for_publish(self.layer, selected_only=self.selected_only)
            self.flattened_fields = export.flattened
            self.source_crs = export.source_crs
            # Only a directory WE created is ever removed. A raster published
            # from the user's own GeoTIFF leaves `workdir` empty, and the
            # cleanup below then has nothing it could reach into.
            self._workdir = export.workdir
            self.setProgress(_EXPORT_SHARE)

            size_bytes = os.path.getsize(export.path)
            # The declared size is what ALIGN checks the organization's
            # remaining space against — before a single byte is signed for.
            # A publish that will not fit is refused here, not after an hour.
            session_info = api.open_upload_session(
                self.session,
                self.org_id,
                export.filename,
                export.content_type,
                size_bytes,
                already_servable=export.already_servable,
            )
            self.asset_id = session_info["assetId"]

            uploader = ResumableUploader(
                export.path,
                session_info["sessionUrl"],
                session_info["chunkSizeBytes"],
            )
            uploader.upload(
                on_progress=self._on_upload_progress,
                is_cancelled=self.isCanceled,
            )

            catalogued = api.finalize(
                self.session,
                self.org_id,
                self.asset_id,
                export.filename,
                self.asset_name,
                provenance=self._provenance(),
                description=self.description,
                visibility=self.visibility,
                already_servable=export.already_servable,
                style=self.style,
                iso=self.iso,
            )
            self._read_derivation(catalogued)
            self.locked_fields = locked_fields_of(self.iso, catalogued)
            self.setProgress(100)
            return True

        except UploadCancelled:
            self.was_cancelled = True
            return False
        except (ExportFailed, UploadFailed) as exc:
            self.error_message = str(exc)
            return False
        except api.ApiError as exc:
            self.error_message = with_capacity(exc.message, exc.status, self.capacity_line)
            self.needs_sign_in = exc.needs_sign_in
            return False
        finally:
            self._cleanup()

    def finished(self, result):
        """Runs on the interface thread. Kept empty on purpose.

        The plugin connects to `taskCompleted`/`taskTerminated` and reads the
        attributes above: putting UI here would tie this class to one caller.
        """
        return

    # -- internals ----------------------------------------------------------

    def _read_derivation(self, catalogued):
        """What ALIGN made of the bytes, as the catalogue answer reports it."""
        if not isinstance(catalogued, dict):
            return
        self.gis_format = catalogued.get("gisFormat")
        self.derivation_error = catalogued.get("derivationError")
        servable = catalogued.get("servableAssetId")
        # Only worth carrying when it points somewhere else: that is the case
        # where the layer to open is NOT the asset this publish created.
        if servable and servable != self.asset_id:
            self.servable_asset_id = servable

    def _on_upload_progress(self, sent, total):
        if total <= 0:
            return
        self.setProgress(_EXPORT_SHARE + (sent / float(total)) * (100.0 - _EXPORT_SHARE))

    def _provenance(self):
        """What ALIGN records about where this asset came from.

        Enough to recognise later that *this* layer, from *this* client, has
        already been published — and nothing more (data minimisation).
        """
        from qgis.core import Qgis

        provenance = {
            "source": "qgis-plugin",
            "sourceLayerId": self.layer_id,
            "clientVersion": plugin_version(),
            "appVersion": Qgis.QGIS_VERSION.split("-")[0],
            "publishedAt": utc_now(),
        }
        if self.source_crs:
            # The system the data was in before leaving: a vector arrives in
            # WGS 84, and this is how anyone can still tell where it came from.
            provenance["sourceCrs"] = self.source_crs
        return provenance

    def _cleanup(self):
        """Remove the exported copy. It was never the user's file."""
        if self._workdir and os.path.isdir(self._workdir):
            shutil.rmtree(self._workdir, ignore_errors=True)
            self._workdir = None


def utc_now():
    """An ISO-8601 UTC timestamp, the shape the API validates against."""
    from datetime import datetime

    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
