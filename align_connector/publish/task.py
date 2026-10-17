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
import time

from qgis.core import QgsTask

from .. import api
from ..i18n import tr
from ..qtcompat import qt_enum
from ..metadata import plugin_version
from .exporter import ExportFailed, export_for_publish
from .feature_service import ServicePublishFailed, follow, pick_layer
from .uploader import ResumableUploader, UploadCancelled, UploadFailed

#: How the export is weighted against the transfer in the progress bar. Writing
#: the file is quick; sending it is what takes the time.
_EXPORT_SHARE = 5.0

#: In a feature-service publish, where the transfer ends and the service's
#: preparation begins in the progress bar (TCK-2320).
_SERVICE_START = 70.0

#: Seconds between two polls of a feature-service job.
_SERVICE_POLL_SECONDS = 3

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
        replace=None,
        as_service=False,
    ):
        """
        :param replace: the :class:`memory.Link` of the item this layer was
            published as, to replace in place (TCK-2218); ``None`` publishes a
            new item.
        :param as_service: publish the layer as an editable feature service
            (TCK-2320): the file is sent in the layer's own projection, then
            ALIGN moves its features into the organization's database and
            removes the file; a replacement does not apply.
        """
        super().__init__(
            ("Replacing “{}” on ALIGN" if replace is not None else "Publishing “{}” to ALIGN").format(asset_name),
            qt_enum(QgsTask, "Flag", "CanCancel"),
        )
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
        #: ALIGN's status and code behind a refusal, for the diagnostic (TCK-2219).
        self.error_status = None
        self.error_code = None
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
        #: The item to replace in place, or None (TCK-2218).
        self.replace = replace
        #: Set on completion: the item was replaced (same id), or the item the
        #: layer remembered was gone and a new one was published instead.
        self.replaced = False
        self.replaced_missing = False
        #: What changed in the attribute schema, as ALIGN read it off the two
        #: files: ``{"added", "removed", "retyped"}`` lists, or None.
        self.schema_changes = None
        #: A feature-service publish (TCK-2320), and what came of it: the
        #: service, its layer, its storage projection and what to tell the
        #: user. `service_pending`: the follow was cancelled, the service will
        #: still appear in the Datastore when ALIGN has prepared it.
        self.as_service = bool(as_service)
        if self.as_service:
            self.replace = None
        self.service_id = None
        self.service_layer_id = None
        self.service_srid = None
        self.service_result = None
        self.service_pending = False
        #: The file is on ALIGN and its conversion asked: a failure from here
        #: on leaves the file stored, and the message says so.
        self._service_stage = False

        self._workdir = None

    # -- worker thread ------------------------------------------------------

    def run(self):
        try:
            export = export_for_publish(self.layer, selected_only=self.selected_only, as_service=self.as_service)
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
            replace_session = self._open_replacement(export, size_bytes) if self.replace is not None else None
            if replace_session is not None:
                catalogued = self._replace(export, replace_session)
            else:
                catalogued = self._publish_new(export, size_bytes)
            self._read_derivation(catalogued)
            self.locked_fields = locked_fields_of(self.iso, catalogued)
            if self.as_service:
                self._service_stage = True
                self._publish_service()
            self.setProgress(100)
            return True

        except UploadCancelled:
            self.was_cancelled = True
            return False
        except (ExportFailed, UploadFailed, ServicePublishFailed) as exc:
            self.error_message = self._service_failure(str(exc))
            return False
        except api.ApiError as exc:
            self.error_message = self._service_failure(with_capacity(exc.message, exc.status, self.capacity_line))
            self.error_status = exc.status
            self.error_code = exc.code
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

    def _publish_new(self, export, size_bytes):
        """A new item: session, transfer, catalogue."""
        session_info = api.open_upload_session(
            self.session,
            self.org_id,
            export.filename,
            export.content_type,
            size_bytes,
            already_servable=export.already_servable,
        )
        self.asset_id = session_info["assetId"]
        self._send(export, session_info)
        return api.finalize(
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

    def _open_replacement(self, export, size_bytes):
        """Ask ALIGN to replace the remembered item, or ``None`` when that item
        is gone (TCK-2218): the layer is then published as a new item, and the
        outcome says so. Any other refusal — not the creator, another format,
        a kind ALIGN converts — stops the publish with ALIGN's reason, so the
        publisher chooses knowingly between retrying and publishing as new."""
        try:
            return api.open_replace_session(
                self.session,
                self.replace.org_id,
                self.replace.asset_id,
                export.filename,
                export.content_type,
                size_bytes,
            )
        except api.ApiError as exc:
            if exc.status == 404 and exc.code == "DATASTORE_ASSET_NOT_FOUND":
                self.replaced_missing = True
                return None
            raise

    def _replace(self, export, session_info):
        """The remembered item, rewritten in place: transfer to the transit
        object, then the swap under the generation ALIGN read at open."""
        self.asset_id = self.replace.asset_id
        self._send(export, session_info)
        catalogued = api.finalize_replace(
            self.session,
            self.replace.org_id,
            self.replace.asset_id,
            session_info["transitFilename"],
            session_info["baseGeneration"],
            name=self.asset_name,
            style=self.style,
            iso=self.iso,
        )
        self.replaced = True
        if isinstance(catalogued, dict):
            changes = catalogued.get("schemaChanges")
            # A dict, even of empty lists, means ALIGN compared the two files;
            # its absence means the format carries no schema it could read.
            if isinstance(changes, dict):
                self.schema_changes = changes
        return catalogued

    def _send(self, export, session_info):
        uploader = ResumableUploader(
            export.path,
            session_info["sessionUrl"],
            session_info["chunkSizeBytes"],
        )
        uploader.upload(
            on_progress=self._on_upload_progress,
            is_cancelled=self.isCanceled,
        )

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
        end = _SERVICE_START if self.as_service else 100.0
        self.setProgress(_EXPORT_SHARE + (sent / float(total)) * (end - _EXPORT_SHARE))

    def _publish_service(self):
        """The uploaded file becomes a feature service (TCK-2320).

        ALIGN reads the file's layer, then prepares the service in the
        background; the task follows the job and reports what it made. A
        cancellation stops the follow, not the job.
        """
        layer = pick_layer(api.preview_feature_service(self.session, self.org_id, self.asset_id))
        job = api.start_feature_service(
            self.session, self.org_id, self.asset_id, layer.get("name") or "", self.asset_name, visibility=self.visibility
        )
        self.service_id = job.get("serviceId")

        def on_progress(loaded, total):
            if total:
                self.setProgress(_SERVICE_START + min(1.0, loaded / float(total)) * (99.0 - _SERVICE_START))

        done = follow(
            lambda: api.feature_service_job(self.session, self.org_id, job["jobId"]),
            wait=lambda: time.sleep(_SERVICE_POLL_SECONDS),
            cancelled=self.isCanceled,
            on_progress=on_progress,
        )
        if done is None:
            self.service_pending = True
            return
        result = done.get("result") if isinstance(done.get("result"), dict) else {}
        self.service_result = result
        self.service_layer_id = result.get("layerId")
        self.service_srid = result.get("storageSrid")

    def _service_failure(self, message):
        if not self._service_stage:
            return message
        return tr("“{}” is stored in your Datastore, but it did not become a feature service: {}").format(
            self.asset_name, message
        )

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
